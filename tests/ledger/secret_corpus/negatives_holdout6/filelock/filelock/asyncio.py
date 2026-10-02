"""An asyncio-based implementation of the file lock."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from dataclasses import dataclass
from inspect import iscoroutinefunction
from threading import local
from typing import TYPE_CHECKING, Final, NoReturn, TypeVar, cast

from ._api import (
    _UNSET_FILE_MODE,
    BaseFileLock,
    CloseErrorPolicy,
    ContextErrorPolicy,
    FileLockContext,
    FileLockMeta,
    _append_exception_context,
    _canonical,
    _ExtraValue,
    _fork_transition,
    _grouped_errors,
    _raise_body_and_release,
    _raise_chained_errors,
    _raise_cleanup_errors,
    _raise_grouped_errors,
    _register_fork_object,
    _resolve_poll_interval,
)
from ._async import (
    _AsyncTransitionGate,
    _AsyncTransitionUnavailableError,
    _BackendOutcome,
    _capture_awaitable,
    _capture_call,
    _drain_future,
    _future_result,
    _wait_until_done,
)
from ._error import Timeout
from ._lease import SoftFileLease
from ._soft import SoftFileLock
from ._strict import StrictSoftFileLock
from ._unix import UnixFileLock
from ._windows import WindowsFileLock

if TYPE_CHECKING:
    import sys
    from collections.abc import Awaitable, Callable, Coroutine, Hashable
    from concurrent import futures
    from types import TracebackType

    if sys.version_info >= (3, 11):  # pragma: no cover (py311+)
        from typing import Self
    else:  # pragma: no cover (<py311)
        from typing_extensions import Self


_LOGGER: Final[logging.Logger] = logging.getLogger("filelock")
_ASYNC_RELEASE_CANCELLATION_ERRORS: Final[str] = "lock release cancellation and backend release both failed"
_ASYNC_CONTEXT_RELEASE_ERRORS: Final[str] = "context body, release cancellation, and backend release failed"
_ASYNC_RELEASE_CANCELLATION_MARKER_ATTR: Final[str] = "_filelock_async_release_cancellation"
_ASYNC_RELEASE_CANCELLATION_MARKER: Final[list[None]] = []

_AT = TypeVar("_AT", bound="BaseAsyncFileLock")


class AsyncFileLockMeta(FileLockMeta):
    def __call__(  # ruff:ignore[too-many-arguments]  # forwards the public constructor's documented parameters
        cls: type[_AT],  # ruff:ignore[invalid-first-argument-name-for-method]  # metaclass __call__ receives the class being constructed
        lock_file: str | os.PathLike[str],
        timeout: float = -1,
        mode: int = _UNSET_FILE_MODE,
        thread_local: bool = False,  # ruff:ignore[boolean-type-hint-positional-argument, boolean-default-value-positional-argument]  # public API: positional bool kept for backwards compatibility
        *,
        blocking: bool = True,
        is_singleton: bool = False,
        poll_interval: float = 0.05,
        lifetime: float | None = None,
        context_error_policy: ContextErrorPolicy = "chain",
        close_error_policy: CloseErrorPolicy = "default",
        fallback_to_soft: bool = True,
        preserve_lock_file: bool = False,
        on_acquired: Callable[[int], None] | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
        run_in_executor: bool = True,
        executor: futures.Executor | None = None,
        **kwargs: _ExtraValue,
    ) -> _AT:
        if thread_local and run_in_executor:
            msg = "run_in_executor is not supported when thread_local is True"
            raise ValueError(msg)
        return super().__call__(  # a subclass may add options of its own, as AsyncSoftFileLease does
            **kwargs,
            lock_file=lock_file,
            timeout=timeout,
            mode=mode,
            thread_local=thread_local,
            blocking=blocking,
            is_singleton=is_singleton,
            poll_interval=poll_interval,
            lifetime=lifetime,
            context_error_policy=context_error_policy,
            close_error_policy=close_error_policy,
            fallback_to_soft=fallback_to_soft,
            preserve_lock_file=preserve_lock_file,
            on_acquired=on_acquired,
            loop=loop,
            run_in_executor=run_in_executor,
            executor=executor,
        )


class BaseAsyncFileLock(BaseFileLock, metaclass=AsyncFileLockMeta):
    """
    Base class for asynchronous file locks.

    .. versionadded:: 3.15.0

    """

    _deadlock_holder_desc: str = "BaseAsyncFileLock instance in this task"
    _constructor_lifetime_warning_stacklevel: int = 4

    @staticmethod
    def _deadlock_scope() -> Hashable | None:
        # One event loop thread runs every task, so a thread-scoped registry cannot tell a same-task reacquire
        # (a real deadlock: the polling task never reaches its own release) from another task queuing behind the
        # holder (no deadlock: each poll yields, so the holder runs on and releases). Only the first may fail
        # fast, so scope holders to the task.
        return asyncio.current_task()

    def __init__(  # ruff:ignore[too-many-arguments]  # public constructor: one parameter per documented lock option
        self,
        lock_file: str | os.PathLike[str],
        timeout: float = -1,
        mode: int = _UNSET_FILE_MODE,
        thread_local: bool = False,  # ruff:ignore[boolean-type-hint-positional-argument, boolean-default-value-positional-argument]  # public API: positional bool kept for backwards compatibility
        *,
        blocking: bool = True,
        is_singleton: bool = False,
        poll_interval: float = 0.05,
        lifetime: float | None = None,
        context_error_policy: ContextErrorPolicy = "chain",
        close_error_policy: CloseErrorPolicy = "default",
        fallback_to_soft: bool = True,
        preserve_lock_file: bool = False,
        on_acquired: Callable[[int], None] | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
        run_in_executor: bool = True,
        executor: futures.Executor | None = None,
    ) -> None:
        """
        Create a new lock object.

        :param lock_file: path to the file
        :param timeout: default timeout when acquiring the lock, in seconds. It will be used as fallback value in the
            acquire method, if no timeout value (``None``) is given. If you want to disable the timeout, set it to a
            negative value. A timeout of 0 means that there is exactly one attempt to acquire the file lock.
        :param mode: file permissions for the lockfile. When not specified, the OS controls permissions via umask and
            default ACLs, preserving POSIX default ACL inheritance in shared directories.
        :param thread_local: Whether this object's internal context should be thread local or not. If this is set to
            ``False`` then the lock will be reentrant across threads. When ``True`` (the default), **all fields of the
            lock's internal context are per-thread**, including the configuration values ``poll_interval``, ``timeout``,
            ``blocking``, ``mode``, and ``lifetime``. Setting one of these properties from one thread does not change
            the value seen by another thread; threads that did not perform the write continue to see the value supplied
            at construction time. ``mode`` has no setter, so construction is the only place it is ever set. If you need
            configuration values to be visible across threads, construct the lock with ``thread_local=False``.
        :param blocking: whether the lock should be blocking or not
        :param is_singleton: If this is set to ``True`` then only one instance of this class will be created per lock
            file. This is useful if you want to use the lock object for reentrant locking without needing to pass the
            same object around.
        :param poll_interval: default interval for polling the lock file, in seconds. It will be used as fallback value
            in the acquire method, if no poll_interval value (``None``) is given.
        :param lifetime: for :class:`AsyncSoftFileLock`, the age in seconds after which a waiting process may delete
            the marker, even while its holder remains alive. This legacy expiry mode does not provide strict mutual
            exclusion. ``None`` (the default) disables age-based expiry. Native OS locks (:class:`AsyncFileLock`)
            cannot be revoked by file age and ignore a non-``None`` ``lifetime`` with a warning.
        :param context_error_policy: how a context manager reconciles a failure in its body with a failure while
            releasing on exit. ``"chain"`` (the default) keeps Python's behavior: the release error propagates with the
            body error in its ``__context__``. ``"group"`` raises a :class:`BaseExceptionGroup` holding the body error
            first and the release error second, so neither hides the other.
        :param close_error_policy: for native locks (:class:`AsyncFileLock`), what to do with an ``os.close`` failure
            after the OS unlock has already committed. ``"default"`` keeps each platform's historical behavior,
            ``"raise"`` always propagates the ``OSError``, and ``"suppress"`` always ignores it.
        :param fallback_to_soft: for :class:`AsyncFileLock`, whether to fall back to soft existence locking when
            ``flock`` returns ``ENOSYS``. ``True`` (default) keeps the fallback; ``False`` propagates the error.
        :param preserve_lock_file: for native locks (:class:`AsyncFileLock`), whether filelock promises not to unlink
            the lock pathname on release. ``False`` (default) keeps each backend's cleanup; ``True`` keeps a stable file
            identity (Windows skips its unlink, Unix refuses the ``ENOSYS`` soft fallback). :class:`AsyncSoftFileLock`
            rejects ``True``.
        :param on_acquired: for native locks (:class:`AsyncFileLock`), a callable invoked with the borrowed lock
            descriptor once per physical acquisition, after the lock is held but before
            :meth:`~BaseAsyncFileLock.acquire` returns. With ``run_in_executor=True`` (the default) it runs in the
            backend executor. It must not close or unlock the descriptor; a raise rolls the acquisition back.
            :class:`AsyncSoftFileLock` rejects it.
        :param loop: The event loop to use. If not specified, the running event loop will be used.
        :param run_in_executor: If this is set to ``True`` then the lock will be acquired in an executor.
        :param executor: The executor to use. If not specified, the default executor will be used.

        """
        self._creator_pid = os.getpid()
        self._is_thread_local = thread_local
        self._is_singleton = is_singleton
        self._context_error_policy = context_error_policy  # already validated by the metaclass
        self._close_error_policy = close_error_policy  # already validated by the metaclass
        self._fallback_to_soft = fallback_to_soft
        self._preserve_lock_file = preserve_lock_file  # already validated by the metaclass
        self._on_acquired = on_acquired  # already validated by the metaclass
        self._transition_gate: Final[_AsyncTransitionGate] = _AsyncTransitionGate()

        self._context: AsyncFileLockContext = (AsyncThreadLocalFileContext if thread_local else AsyncFileLockContext)(
            lock_file=os.fspath(lock_file),
            timeout=timeout,
            mode=mode,
            blocking=blocking,
            poll_interval=poll_interval,
            lifetime=lifetime,
            loop=loop,
            run_in_executor=run_in_executor,
            executor=executor,
        )
        _register_fork_object(self)

    @property
    def run_in_executor(self) -> bool:
        """Whether run in executor."""
        return self._context.run_in_executor

    @property
    def executor(self) -> futures.Executor | None:
        """The executor."""
        return self._context.executor

    @executor.setter
    def executor(self, value: futures.Executor | None) -> None:  # pragma: no cover
        """
        Change the executor.

        :param futures.Executor | None value: the new executor or ``None``

        """
        self._context.executor = value

    @property
    def loop(self) -> asyncio.AbstractEventLoop | None:
        """The event loop."""
        return self._context.loop

    async def acquire(  # ty: ignore[invalid-method-override]
        self,
        timeout: float | None = None,
        poll_interval: float | None = None,
        *,
        blocking: bool | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> AsyncAcquireReturnProxy:
        """
        Try to acquire the file lock.

        :param timeout: maximum wait time for acquiring the lock, ``None`` means use the default
            :attr:`~BaseFileLock.timeout` is and if ``timeout < 0``, there is no timeout and this method will block
            until the lock could be acquired
        :param poll_interval: interval of trying to acquire the lock file, ``None`` means use the default
            :attr:`~BaseFileLock.poll_interval`
        :param blocking: whether to retry until this call holds the lock or the timeout expires, ``None`` means use
            the default :attr:`~BaseFileLock.blocking`. ``False`` makes one attempt and raises :class:`~Timeout` on
            failure.
        :param cancel_check: a callable returning ``True`` when the acquisition should be canceled. Checked on each poll
            iteration. When triggered, raises :class:`~Timeout` just like an expired timeout.

        :returns: a context object that will unlock the file when the context is exited

        :raises Timeout: if fails to acquire lock within the timeout period

        .. code-block:: python

            # You can use this method in the context manager (recommended)
            with lock.acquire():
                pass

            # Or use an equivalent try-finally construct:
            lock.acquire()
            try:
                pass
            finally:
                lock.release()

        """
        self._raise_if_inherited()
        if timeout is None:
            timeout = self._context.timeout

        if blocking is None:
            blocking = self._context.blocking

        if poll_interval is None:
            poll_interval = self._context.poll_interval
        poll_interval = _resolve_poll_interval(poll_interval)

        start_time = time.perf_counter()
        try:
            return await self._acquire_with_admission(
                blocking=blocking,
                cancel_check=cancel_check,
                timeout=timeout,
                poll_interval=poll_interval,
                start_time=start_time,
            )
        except _AsyncTransitionUnavailableError:
            raise Timeout(self.lock_file) from None

    async def _acquire_with_admission(
        self,
        *,
        blocking: bool,
        cancel_check: Callable[[], bool] | None,
        timeout: float,
        poll_interval: float,
        start_time: float,
    ) -> AsyncAcquireReturnProxy:
        async with self._transition_gate.hold_for_acquire(
            blocking=blocking,
            cancel_check=cancel_check,
            deadline=None if timeout < 0 else start_time + timeout,
            poll_interval=poll_interval,
        ):
            # A canceled provisional acquire must finish rollback before another caller can claim its descriptor.
            canonical = _canonical(self.lock_file)
            self._context.lock_counter += 1
            self._raise_if_would_deadlock(canonical, timeout=timeout, blocking=blocking)
            self._context.claim_root = canonical
            try:
                await self._async_poll_until_acquired(
                    blocking=blocking,
                    cancel_check=cancel_check,
                    timeout=timeout,
                    poll_interval=poll_interval,
                    start_time=start_time,
                )
            except BaseException:
                self._reconcile_failed_acquire(canonical)
                raise
            finally:
                self._context.claim_root = None
            self._commit_acquire(canonical)
            return AsyncAcquireReturnProxy(lock=self)

    async def _async_poll_until_acquired(
        self,
        *,
        blocking: bool,
        cancel_check: Callable[[], bool] | None,
        timeout: float,
