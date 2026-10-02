from __future__ import annotations

import array
import math
import os
import socket
import sys
import types
import weakref
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Awaitable,
    Callable,
    Collection,
    Coroutine,
    Iterable,
    Sequence,
)
from contextlib import AbstractContextManager
from contextvars import Context
from dataclasses import dataclass
from functools import partial, wraps
from io import IOBase
from os import PathLike
from signal import Signals
from socket import AddressFamily, SocketKind
from types import TracebackType
from typing import (
    IO,
    TYPE_CHECKING,
    Any,
    Generic,
    Literal,
    NoReturn,
    ParamSpec,
    TypeVar,
    cast,
    overload,
)

import trio.from_thread
import trio.lowlevel
from outcome import Error, Outcome, Value
from trio.lowlevel import (
    current_root_task,
    current_task,
    notify_closing,
    wait_readable,
    wait_writable,
)
from trio.socket import SocketType as TrioSocketType
from trio.to_thread import run_sync

from .. import (
    CapacityLimiterStatistics,
    EventStatistics,
    LockStatistics,
    RunFinishedError,
    TaskInfo,
    WouldBlock,
    abc,
)
from .._core._eventloop import claim_worker_thread
from .._core._exceptions import (
    BrokenResourceError,
    BusyResourceError,
    ClosedResourceError,
    EndOfStream,
)
from .._core._sockets import convert_ipv6_sockaddr
from .._core._streams import create_memory_object_stream
from .._core._synchronization import (
    CapacityLimiter as BaseCapacityLimiter,
)
from .._core._synchronization import Event as BaseEvent
from .._core._synchronization import Lock as BaseLock
from .._core._synchronization import (
    ResourceGuard,
    SemaphoreStatistics,
)
from .._core._synchronization import Semaphore as BaseSemaphore
from .._core._tasks import CancelScope as BaseCancelScope
from .._core._tasks import TaskHandle
from ..abc import IPSockAddrType, UDPPacketType, UNIXDatagramPacketType
from ..abc._eventloop import AsyncBackend, StrOrBytesPath
from ..abc._tasks import T_contra, call_for_coroutine, get_callable_name, get_coro_name

if TYPE_CHECKING:
    from _typeshed import FileDescriptorLike

    from ..streams.memory import MemoryObjectSendStream

if sys.version_info < (3, 15):
    from typing_extensions import sentinel

if sys.version_info >= (3, 11):
    from typing import Self, TypeVarTuple, Unpack
else:
    from exceptiongroup import BaseExceptionGroup
    from typing_extensions import Self, TypeVarTuple, Unpack

T = TypeVar("T")
T_Retval = TypeVar("T_Retval")
T_co = TypeVar("T_co", covariant=True)
T_SockAddr = TypeVar("T_SockAddr", str, IPSockAddrType)
PosArgsT = TypeVarTuple("PosArgsT")
P = ParamSpec("P")


def ensure_returns_coro(
    func: Callable[P, Awaitable[T_Retval]],
) -> Callable[P, Coroutine[Any, Any, T_Retval]]:
    @wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> Coroutine[Any, Any, T_Retval]:
        awaitable = func(*args, **kwargs)
        # Check the common case first.
        if isinstance(awaitable, Coroutine):
            return awaitable
        elif not isinstance(awaitable, Awaitable):
            # The user violated the type annotations. Still, we should pass this on to
            # Trio so it can raise with an appropriate message.
            return awaitable
        else:

            @wraps(func)
            async def inner_wrapper() -> T_Retval:
                return await awaitable

            return inner_wrapper()

    return wrapper


#
# Event loop
#

RunVar = trio.lowlevel.RunVar


#
# Timeouts and cancellation
#


class CancelScope(BaseCancelScope):
    __slots__ = ("__original",)

    def __new__(
        cls, original: trio.CancelScope | None = None, **kwargs: object
    ) -> Self:
        return object.__new__(cls)

    def __init__(self, original: trio.CancelScope | None = None, **kwargs: Any) -> None:
        self.__original = original or trio.CancelScope(**kwargs)

    def __enter__(self) -> Self:
        self.__original.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        return self.__original.__exit__(exc_type, exc_val, exc_tb)

    def cancel(self, reason: str | None = None) -> None:
        self.__original.cancel(reason)

    @property
    def deadline(self) -> float:
        return self.__original.deadline

    @deadline.setter
    def deadline(self, value: float) -> None:
        self.__original.deadline = value

    @property
    def cancel_called(self) -> bool:
        return self.__original.cancel_called

    @property
    def cancelled_caught(self) -> bool:
        return self.__original.cancelled_caught

    @property
    def shield(self) -> bool:
        return self.__original.shield

    @shield.setter
    def shield(self, value: bool) -> None:
        self.__original.shield = value


#
# Task groups
#

empty_start_value = sentinel("empty_start_value")


class _TrioTaskStatus(abc.TaskStatus[T_contra], Generic[T_contra]):
    early_start_value: T_contra | object = empty_start_value
    real_task_status: trio.TaskStatus[T_contra | None] | None = None

    def started(self, value: T_contra | None = None) -> None:
        if self.real_task_status is None:
            if self.early_start_value is not empty_start_value:
                raise RuntimeError("called 'started' twice on the same task status")

            self.early_start_value = value
        else:
            self.real_task_status.started(value)


class TaskGroup(abc.TaskGroup):
    def __init__(self) -> None:
        self._entered = False
        self._active = False
        self._nursery_manager = trio.open_nursery(strict_exception_groups=True)
        self.cancel_scope = None  # type: ignore[assignment]

    async def __aenter__(self) -> Self:
        if self._entered:
            raise RuntimeError("TaskGroup cannot be entered more than once")

        self._entered = True
        self._active = True
        self._nursery = await self._nursery_manager.__aenter__()
        self.cancel_scope = CancelScope(self._nursery.cancel_scope)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        try:
            # trio.Nursery.__exit__ returns bool; .open_nursery has wrong type
            return await self._nursery_manager.__aexit__(exc_type, exc_val, exc_tb)  # type: ignore[return-value]
        except BaseExceptionGroup as exc:
            if not exc.split(trio.Cancelled)[1]:
                raise trio.Cancelled._create() from exc

            raise
        finally:
            del exc_val, exc_tb
            self._active = False

    def _check_active(self, coro: Coroutine | None = None) -> None:
        if not self._active:
            if coro is not None:
                coro.close()

            raise RuntimeError(
                "This task group is not active; no new tasks can be started."
            )

    def create_task(
        self,
        coro: Coroutine[Any, Any, T_co],
        *,
        name: object = None,
        context: Context | None = None,
    ) -> TaskHandle[T_co]:
        if not isinstance(coro, Coroutine):
            raise TypeError(f"expected a coroutine, got {coro.__class__.__qualname__}")

        self._check_active(coro)
        final_name = get_coro_name(coro, name)
        handle = TaskHandle(coro, final_name)
        if context is not None:
            context.run(
                partial(self._nursery.start_soon, handle._run_coro, name=handle.name)
            )
        else:
            self._nursery.start_soon(handle._run_coro, name=handle.name)

        return handle

    async def start(
        self,
        func: Callable[[Unpack[PosArgsT]], Coroutine[Any, Any, T_co]],
        *args: Unpack[PosArgsT],
        name: object = None,
        return_handle: Literal[False, True] = False,
    ) -> Any:
        handle: TaskHandle[T_co]

        async def run_coro_with_task_status(
            *, task_status: trio.TaskStatus[Any]
        ) -> None:
            nonlocal handle
            wrapper_task_status = _TrioTaskStatus()
            coro = call_for_coroutine(func, args, task_status=wrapper_task_status)
            if wrapper_task_status.early_start_value is not empty_start_value:
                task_status.started(wrapper_task_status.early_start_value)
            else:
                wrapper_task_status.real_task_status = task_status

            handle = TaskHandle(coro, final_name)
            await handle._run_coro()

        self._check_active()
        final_name = get_callable_name(func, name)
        start_value = await self._nursery.start(
            run_coro_with_task_status, name=final_name
        )
        if return_handle:
            handle._start_value = start_value
            return handle
        else:
            return start_value


#
# Subprocesses
#


@dataclass(eq=False)
class ReceiveStreamWrapper(abc.ByteReceiveStream):
    _stream: trio.abc.ReceiveStream

    async def receive(self, max_bytes: int | None = None) -> bytes:
        if max_bytes is not None and max_bytes < 1:
            raise ValueError("max_bytes must be a positive integer")

        try:
            data = await self._stream.receive_some(max_bytes)
        except trio.ClosedResourceError as exc:
            raise ClosedResourceError from exc.__cause__
        except trio.BrokenResourceError as exc:
            raise BrokenResourceError from exc.__cause__

        if data:
            return bytes(data)
        else:
            raise EndOfStream

    async def aclose(self) -> None:
        await self._stream.aclose()


@dataclass(eq=False)
class SendStreamWrapper(abc.ByteSendStream):
    _stream: trio.abc.SendStream

    async def send(self, item: bytes) -> None:
        try:
            await self._stream.send_all(item)
        except trio.ClosedResourceError as exc:
            raise ClosedResourceError from exc.__cause__
        except trio.BrokenResourceError as exc:
            raise BrokenResourceError from exc.__cause__

    async def aclose(self) -> None:
        await self._stream.aclose()


@dataclass(eq=False)
class Process(abc.Process):
    _process: trio.Process
    _stdin: abc.ByteSendStream | None
    _stdout: abc.ByteReceiveStream | None
    _stderr: abc.ByteReceiveStream | None

    async def aclose(self) -> None:
        with CancelScope(shield=True):
            if self._stdin:
                await self._stdin.aclose()
            if self._stdout:
                await self._stdout.aclose()
            if self._stderr:
                await self._stderr.aclose()

        try:
            await self.wait()
        except BaseException:
            self.kill()
            with CancelScope(shield=True):
                await self.wait()
            raise

    async def wait(self) -> int:
        return await self._process.wait()

    def terminate(self) -> None:
        self._process.terminate()

    def kill(self) -> None:
        self._process.kill()

    def send_signal(self, signal: Signals) -> None:
        self._process.send_signal(signal)

    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def returncode(self) -> int | None:
        return self._process.returncode

    @property
    def stdin(self) -> abc.ByteSendStream | None:
        return self._stdin

    @property
    def stdout(self) -> abc.ByteReceiveStream | None:
        return self._stdout

    @property
    def stderr(self) -> abc.ByteReceiveStream | None:
        return self._stderr


class _ProcessPoolShutdownInstrument(trio.abc.Instrument):
    def after_run(self) -> None:
        super().after_run()


current_default_worker_process_limiter: trio.lowlevel.RunVar = RunVar(
    "current_default_worker_process_limiter"
)


async def _shutdown_process_pool(workers: set[abc.Process]) -> None:
    try:
        await trio.sleep(math.inf)
    except trio.Cancelled:
        for process in workers:
            if process.returncode is None:
                process.kill()

        with CancelScope(shield=True):
            for process in workers:
                await process.aclose()


#
# Sockets and networking
#


class _TrioSocketMixin(Generic[T_SockAddr]):
    def __init__(self, trio_socket: TrioSocketType) -> None:
        self._trio_socket = trio_socket
        self._closed = False

    def _check_closed(self) -> None:
        if self._closed:
            raise ClosedResourceError
        if self._trio_socket.fileno() < 0:
            raise BrokenResourceError

    @property
    def _raw_socket(self) -> socket.socket:
        return self._trio_socket._sock  # type: ignore[attr-defined]

    async def aclose(self) -> None:
        if self._trio_socket.fileno() >= 0:
            self._closed = True
            self._trio_socket.close()

    def _convert_socket_error(self, exc: BaseException) -> NoReturn:
        if isinstance(exc, trio.ClosedResourceError):
            raise ClosedResourceError from exc
        elif self._trio_socket.fileno() < 0 and self._closed:
            raise ClosedResourceError from None
        elif isinstance(exc, OSError):
            raise BrokenResourceError from exc
        else:
            raise exc


class SocketStream(_TrioSocketMixin, abc.SocketStream):
    def __init__(self, trio_socket: TrioSocketType) -> None:
        super().__init__(trio_socket)
        self._receive_guard = ResourceGuard("reading from")
        self._send_guard = ResourceGuard("writing to")

    async def receive(self, max_bytes: int = 65536) -> bytes:
        if max_bytes < 1:
            raise ValueError("max_bytes must be a positive integer")

        with self._receive_guard:
            try:
                data = await self._trio_socket.recv(max_bytes)
            except BaseException as exc:
                self._convert_socket_error(exc)

            if data:
                return data
            else:
                raise EndOfStream

    async def send(self, item: bytes) -> None:
        with self._send_guard:
            view = memoryview(item)
            while view:
                try:
                    bytes_sent = await self._trio_socket.send(view)
                except BaseException as exc:
                    self._convert_socket_error(exc)

                view = view[bytes_sent:]

    async def send_eof(self) -> None:
        self._trio_socket.shutdown(socket.SHUT_WR)


class UNIXSocketStream(SocketStream, abc.UNIXSocketStream):
    async def receive_fds(self, msglen: int, maxfds: int) -> tuple[bytes, list[int]]:
        if not isinstance(msglen, int) or msglen < 0:
            raise ValueError("msglen must be a non-negative integer")
        if not isinstance(maxfds, int) or maxfds < 1:
            raise ValueError("maxfds must be a positive integer")

        fds = array.array("i")
        await trio.lowlevel.checkpoint()
        with self._receive_guard:
            while True:
                try:
                    message, ancdata, _flags, _addr = await self._trio_socket.recvmsg(
                        msglen, socket.CMSG_LEN(maxfds * fds.itemsize)
                    )
                except BaseException as exc:
                    self._convert_socket_error(exc)
                else:
                    if not message and not ancdata:
                        raise EndOfStream

                    break

        for cmsg_level, cmsg_type, cmsg_data in ancdata:
            if cmsg_level != socket.SOL_SOCKET or cmsg_type != socket.SCM_RIGHTS:
                raise RuntimeError(
                    f"Received unexpected ancillary data; message = {message!r}, "
                    f"cmsg_level = {cmsg_level}, cmsg_type = {cmsg_type}"
                )

            fds.frombytes(cmsg_data[: len(cmsg_data) - (len(cmsg_data) % fds.itemsize)])

        return message, list(fds)

    async def send_fds(self, message: bytes, fds: Collection[int | IOBase]) -> None:
        if not message:
            raise ValueError("message must not be empty")
        if not fds:
            raise ValueError("fds must not be empty")

        filenos: list[int] = []
        for fd in fds:
