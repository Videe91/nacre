from __future__ import annotations

import array
import asyncio
import concurrent.futures
import math
import os
import socket
import sys
import threading
import weakref
from asyncio import (
    AbstractEventLoop,
    CancelledError,
    all_tasks,
    create_task,
    current_task,
    get_running_loop,
    sleep,
)
from asyncio.base_events import _run_until_complete_cb  # type: ignore[attr-defined]
from collections import OrderedDict, deque
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
from concurrent.futures import Future
from contextlib import AbstractContextManager, suppress
from contextvars import Context, copy_context
from dataclasses import dataclass, field
from functools import partial, wraps
from inspect import (
    CORO_RUNNING,
    CORO_SUSPENDED,
    getcoroutinestate,
)
from io import IOBase
from os import PathLike
from queue import Queue
from signal import Signals
from socket import AddressFamily, SocketKind
from threading import Thread
from types import CodeType, TracebackType
from typing import (
    IO,
    TYPE_CHECKING,
    Any,
    Literal,
    ParamSpec,
    TypeVar,
    cast,
)
from weakref import WeakKeyDictionary

from .. import (
    CapacityLimiterStatistics,
    EventStatistics,
    LockStatistics,
    TaskInfo,
    abc,
)
from .._core._eventloop import (
    claim_worker_thread,
    set_current_async_library,
    threadlocals,
)
from .._core._exceptions import (
    BrokenResourceError,
    BusyResourceError,
    ClosedResourceError,
    EndOfStream,
    RunFinishedError,
    WouldBlock,
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
from ..abc import (
    AsyncBackend,
    IPSockAddrType,
    SocketListener,
    UDPPacketType,
    UNIXDatagramPacketType,
)
from ..abc._tasks import call_for_coroutine, get_callable_name, get_coro_name
from ..lowlevel import RunVar, _run_vars

if TYPE_CHECKING:
    from _typeshed import FileDescriptorLike

    from ..abc._eventloop import StrOrBytesPath
    from ..streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
else:
    FileDescriptorLike = object

if sys.version_info >= (3, 11):
    from asyncio import Runner
    from typing import Self, TypeVarTuple, Unpack
else:
    import contextvars
    import enum
    import signal
    from asyncio import coroutines, events, exceptions, tasks

    from exceptiongroup import BaseExceptionGroup
    from typing_extensions import Self, TypeVarTuple, Unpack

    class _State(enum.Enum):
        CREATED = "created"
        INITIALIZED = "initialized"
        CLOSED = "closed"

    class Runner:
        # Copied from CPython 3.11
        def __init__(
            self,
            *,
            debug: bool | None = None,
            loop_factory: Callable[[], AbstractEventLoop] | None = None,
        ):
            self._state = _State.CREATED
            self._debug = debug
            self._loop_factory = loop_factory
            self._loop: AbstractEventLoop | None = None
            self._context = None
            self._interrupt_count = 0
            self._set_event_loop = False

        def __enter__(self) -> Self:
            self._lazy_init()
            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc_val: BaseException | None,
            exc_tb: TracebackType | None,
        ) -> None:
            self.close()

        def close(self) -> None:
            """Shutdown and close event loop."""
            loop = self._loop
            if self._state is not _State.INITIALIZED or loop is None:
                return
            try:
                _cancel_all_tasks(loop)
                loop.run_until_complete(loop.shutdown_asyncgens())
                if hasattr(loop, "shutdown_default_executor"):
                    loop.run_until_complete(loop.shutdown_default_executor())
                else:
                    loop.run_until_complete(_shutdown_default_executor(loop))
            finally:
                if self._set_event_loop:
                    events.set_event_loop(None)
                loop.close()
                self._loop = None
                self._state = _State.CLOSED

        def get_loop(self) -> AbstractEventLoop:
            """Return embedded event loop."""
            self._lazy_init()
            return self._loop

        def run(self, coro: Coroutine[T_Retval], *, context=None) -> T_Retval:
            """Run a coroutine inside the embedded event loop."""
            if not coroutines.iscoroutine(coro):
                raise ValueError(f"a coroutine was expected, got {coro!r}")

            if events._get_running_loop() is not None:
                # fail fast with short traceback
                raise RuntimeError(
                    "Runner.run() cannot be called from a running event loop"
                )

            self._lazy_init()

            if context is None:
                context = self._context
            task = context.run(self._loop.create_task, coro)

            if (
                threading.current_thread() is threading.main_thread()
                and signal.getsignal(signal.SIGINT) is signal.default_int_handler
            ):
                sigint_handler = partial(self._on_sigint, main_task=task)
                try:
                    signal.signal(signal.SIGINT, sigint_handler)
                except ValueError:
                    # `signal.signal` may throw if `threading.main_thread` does
                    # not support signals (e.g. embedded interpreter with signals
                    # not registered - see gh-91880)
                    sigint_handler = None
            else:
                sigint_handler = None

            self._interrupt_count = 0
            try:
                return self._loop.run_until_complete(task)
            except exceptions.CancelledError:
                if self._interrupt_count > 0:
                    uncancel = getattr(task, "uncancel", None)
                    if uncancel is not None and uncancel() == 0:
                        raise KeyboardInterrupt  # noqa: B904
                raise  # CancelledError
            finally:
                if (
                    sigint_handler is not None
                    and signal.getsignal(signal.SIGINT) is sigint_handler
                ):
                    signal.signal(signal.SIGINT, signal.default_int_handler)

        def _lazy_init(self) -> None:
            if self._state is _State.CLOSED:
                raise RuntimeError("Runner is closed")
            if self._state is _State.INITIALIZED:
                return
            if self._loop_factory is None:
                self._loop = events.new_event_loop()
                if not self._set_event_loop:
                    # Call set_event_loop only once to avoid calling
                    # attach_loop multiple times on child watchers
                    events.set_event_loop(self._loop)
                    self._set_event_loop = True
            else:
                self._loop = self._loop_factory()
            if self._debug is not None:
                self._loop.set_debug(self._debug)
            self._context = contextvars.copy_context()
            self._state = _State.INITIALIZED

        def _on_sigint(self, signum, frame, main_task: asyncio.Task) -> None:
            self._interrupt_count += 1
            if self._interrupt_count == 1 and not main_task.done():
                main_task.cancel()
                # wakeup loop if it is blocked by select() with long timeout
                self._loop.call_soon_threadsafe(lambda: None)
                return
            raise KeyboardInterrupt()

    def _cancel_all_tasks(loop: AbstractEventLoop) -> None:
        to_cancel = tasks.all_tasks(loop)
        if not to_cancel:
            return

        for task in to_cancel:
            task.cancel()

        loop.run_until_complete(tasks.gather(*to_cancel, return_exceptions=True))

        for task in to_cancel:
            if task.cancelled():
                continue
            if task.exception() is not None:
                loop.call_exception_handler(
                    {
                        "message": "unhandled exception during asyncio.run() shutdown",
                        "exception": task.exception(),
                        "task": task,
                    }
                )

    async def _shutdown_default_executor(loop: AbstractEventLoop) -> None:
        """Schedule the shutdown of the default executor."""

        def _do_shutdown(future: asyncio.futures.Future) -> None:
            try:
                loop._default_executor.shutdown(wait=True)  # type: ignore[attr-defined]
                loop.call_soon_threadsafe(future.set_result, None)
            except Exception as ex:
                loop.call_soon_threadsafe(future.set_exception, ex)

        loop._executor_shutdown_called = True
        if loop._default_executor is None:
            return
        future = loop.create_future()
        thread = threading.Thread(target=_do_shutdown, args=(future,))
        thread.start()
        try:
            await future
        finally:
            thread.join()


T_Retval = TypeVar("T_Retval")
T_co = TypeVar("T_co", covariant=True)
T_contra = TypeVar("T_contra", contravariant=True)
PosArgsT = TypeVarTuple("PosArgsT")
P = ParamSpec("P")

_root_task: RunVar[asyncio.Task[Any] | None] = RunVar("_root_task")


def find_root_task() -> asyncio.Task:
    root_task = _root_task.get(None)
    if root_task is not None and not root_task.done():
        return root_task

    # Look for a task that has been started via run_until_complete()
    for task in all_tasks():
        if task._callbacks and not task.done():
            for cb, context in task._callbacks:
                if (
                    cb is _run_until_complete_cb
                    or getattr(cb, "__module__", None) == "uvloop.loop"
                ):
                    _root_task.set(task)

                    def _unset(t: asyncio.Task[Any]) -> None:
                        if vars := _run_vars.get(t.get_loop()):
                            vars.pop(_root_task, None)

                    # Register a callback to break the task -> loop -> _run_var[loop][_root_task] -> task cycle
                    # Also run it in its own context to not create another reference.
                    # We can't use RunVar.reset() here since these are called synchronously
                    # and thus lowlevel.current_token() (which RunVar.reset() depends on) fails.
                    task.add_done_callback(_unset, context=context)
                    return task

    # Look up the topmost task in the AnyIO task tree, if possible
    task = cast(asyncio.Task, current_task())
    state = _task_states.get(task)
    if state:
        cancel_scope = state.cancel_scope
        while cancel_scope and cancel_scope._parent_scope is not None:
            cancel_scope = cancel_scope._parent_scope

        if cancel_scope is not None:
            return cast(asyncio.Task, cancel_scope._host_task)

    return task


def _task_started(task: asyncio.Task) -> bool:
    """Return ``True`` if the task has been started and has not finished."""
    # The task coro should never be None here, as we never add finished tasks to the
    # task list
    coro = task.get_coro()
    assert coro is not None
    return getcoroutinestate(coro) in (CORO_RUNNING, CORO_SUSPENDED)


#
# Timeouts and cancellation
#


def is_anyio_cancellation(exc: CancelledError) -> bool:
    # Sometimes third party frameworks catch a CancelledError and raise a new one, so as
    # a workaround we have to look at the previous ones in __context__ too for a
    # matching cancel message
    while True:
        if (
            exc.args
            and isinstance(exc.args[0], str)
            and exc.args[0].startswith("Cancelled via cancel scope ")
        ):
            return True

        if isinstance(exc.__context__, CancelledError):
            exc = exc.__context__
            continue

        return False


class CancelScope(BaseCancelScope):
    __slots__ = (
        "_active",
        "_cancel_called",
        "_cancel_handle",
        "_cancel_reason",
        "_cancelled_caught",
        "_child_scopes",
        "_deadline",
        "_host_task",
        "_parent_scope",
        "_pending_uncancellations",
        "_shield",
        "_tasks",
        "_timeout_handle",
    )

    def __new__(cls, *, deadline: float = math.inf, shield: bool = False) -> Self:
        return object.__new__(cls)

    def __init__(self, deadline: float = math.inf, shield: bool = False):
        self._deadline = deadline
        self._shield = shield
        self._parent_scope: CancelScope | None = None
        self._child_scopes: set[CancelScope] = set()
        self._cancel_called = False
        self._cancel_reason: str | None = None
        self._cancelled_caught = False
        self._active = False
        self._timeout_handle: asyncio.TimerHandle | None = None
        self._cancel_handle: asyncio.Handle | None = None
        self._tasks: set[asyncio.Task] = set()
        self._host_task: asyncio.Task | None = None
        if sys.version_info >= (3, 11):
            self._pending_uncancellations: int | None = 0
        else:
            self._pending_uncancellations = None

    def __enter__(self) -> Self:
        if self._active:
            raise RuntimeError(
                "Each CancelScope may only be used for a single 'with' block"
            )

        self._host_task = host_task = cast(asyncio.Task, current_task())
        self._tasks.add(host_task)
        try:
            task_state = _task_states[host_task]
        except KeyError:
            task_state = TaskState(None, self)
            _task_states[host_task] = task_state
        else:
            self._parent_scope = task_state.cancel_scope
            task_state.cancel_scope = self
            if self._parent_scope is not None:
                # If using an eager task factory, the parent scope may not even contain
                # the host task
                self._parent_scope._child_scopes.add(self)
                self._parent_scope._tasks.discard(host_task)

        self._timeout()
        self._active = True

        # Start cancelling the host task if the scope was cancelled before entering
        if self._cancel_called:
            self._deliver_cancellation(self)

        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        del exc_tb

        if not self._active:
            raise RuntimeError("This cancel scope is not active")
        if current_task() is not self._host_task:
            raise RuntimeError(
                "Attempted to exit cancel scope in a different task than it was "
                "entered in"
            )

        assert self._host_task is not None
        host_task_state = _task_states.get(self._host_task)
        if host_task_state is None or host_task_state.cancel_scope is not self:
            raise RuntimeError(
                "Attempted to exit a cancel scope that isn't the current tasks's "
                "current cancel scope"
            )

        try:
            self._active = False
            if self._timeout_handle:
                self._timeout_handle.cancel()
                self._timeout_handle = None

            self._tasks.remove(self._host_task)
            if self._parent_scope is not None:
                self._parent_scope._child_scopes.remove(self)
                self._parent_scope._tasks.add(self._host_task)

            host_task_state.cancel_scope = self._parent_scope

