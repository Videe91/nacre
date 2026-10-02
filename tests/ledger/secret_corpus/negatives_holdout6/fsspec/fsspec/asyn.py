import asyncio
import asyncio.events
import concurrent.futures
import functools
import inspect
import io
import logging
import numbers
import os
import re
import sys
import threading
from collections.abc import Iterable
from glob import has_magic
from typing import TYPE_CHECKING

from .callbacks import DEFAULT_CALLBACK
from .exceptions import FSTimeoutError
from .implementations.local import LocalFileSystem, make_path_posix, trailing_sep
from .spec import AbstractBufferedFile, AbstractFileSystem
from .utils import check_contained, glob_translate, is_exception, other_paths

private = re.compile("_[^_]")
iothread = [None]  # dedicated fsspec IO thread
loop = [None]  # global event loop for any non-async instance
_lock = None  # global lock placeholder
get_running_loop = asyncio.get_running_loop


def get_lock():
    """Allocate or return a threading lock.

    The lock is allocated on first use to allow setting one lock per forked process.
    """
    global _lock
    if not _lock:
        _lock = threading.Lock()
    return _lock


def reset_lock():
    """Reset the global lock.

    This should be called only on the init of a forked process to reset the lock to
    None, enabling the new forked process to get a new lock.
    """
    global _lock

    iothread[0] = None
    loop[0] = None
    _lock = None


async def _runner(event, coro, result, timeout=None):
    timeout = timeout if timeout else None  # convert 0 or 0.0 to None
    if timeout is not None:
        coro = asyncio.wait_for(coro, timeout=timeout)
    try:
        result[0] = await coro
    except Exception as ex:
        result[0] = ex
    finally:
        event.set()


def sync(loop, func, *args, timeout=None, **kwargs):
    """
    Make loop run coroutine until it returns. Runs in other thread

    Examples
    --------
    >>> fsspec.asyn.sync(fsspec.asyn.get_loop(), func, *args,
                         timeout=timeout, **kwargs)
    """
    timeout = timeout if timeout else None  # convert 0 or 0.0 to None
    # NB: if the loop is not running *yet*, it is OK to submit work
    # and we will wait for it
    if loop is None or loop.is_closed():
        raise RuntimeError("Loop is not running")
    try:
        loop0 = asyncio.events.get_running_loop()
        if loop0 is loop:
            raise NotImplementedError("Calling sync() from within a running loop")
    except NotImplementedError:
        raise
    except RuntimeError:
        pass
    coro = func(*args, **kwargs)
    result = [None]
    event = threading.Event()
    asyncio.run_coroutine_threadsafe(_runner(event, coro, result, timeout), loop)
    while True:
        # this loops allows thread to get interrupted
        if event.wait(1):
            break
        if timeout is not None:
            timeout -= 1
            if timeout < 0:
                raise FSTimeoutError

    return_result = result[0]
    if isinstance(return_result, asyncio.TimeoutError):
        # suppress asyncio.TimeoutError, raise FSTimeoutError
        raise FSTimeoutError from return_result
    elif isinstance(return_result, BaseException):
        raise return_result
    else:
        return return_result


_deferred_close_tasks = set()
_deferred_close_lock = threading.Lock()


def _on_loop_thread(loop):
    """Returns True if the current thread is servicing the given event loop."""
    if loop is None:
        return False
    try:
        return asyncio.get_running_loop() is loop
    except RuntimeError:
        return False


def _defer_task(
    loop,
    coro,
    description="deferred task",
    logger=None,
    log_level=logging.WARNING,
):
    """Schedules a coroutine as a tracked background task on ``loop``.

    Retains a strong reference in ``_deferred_close_tasks`` until completion to
    prevent asyncio garbage collection from discarding pending tasks mid-flight,
    and ensures unhandled task exceptions are retrieved and logged.
    """
    task = loop.create_task(coro)
    with _deferred_close_lock:
        _deferred_close_tasks.add(task)

    def _on_done(t):
        with _deferred_close_lock:
            _deferred_close_tasks.discard(t)
        if not t.cancelled():
            exc = t.exception()
            if exc:
                log = logger or logging.getLogger("fsspec.asyn")
                log.log(
                    log_level,
                    "%s failed during asynchronous execution: %s",
                    description,
                    exc,
                    exc_info=exc,
                )

    task.add_done_callback(_on_done)
    return task


def sync_teardown(
    loop,
    func_or_coro,
    *args,
    timeout=None,
    description="teardown",
    **kwargs,
):
    """Safely runs an async teardown coroutine on ``loop`` from synchronous context.

    Schedules via :func:`asyncio.run_coroutine_threadsafe` or defers on the loop
    thread to prevent deadlocks.
    """
    coro = func_or_coro(*args, **kwargs) if callable(func_or_coro) else func_or_coro
    if not asyncio.iscoroutine(coro):
        return

    if sys.is_finalizing():
        coro.close()
        return

    if loop is None or not loop.is_running() or loop.is_closed():
        coro.close()
        raise RuntimeError(f"Skipping {description}: no usable IO loop available.")

    if _on_loop_thread(loop):
        _defer_task(loop, coro, description=description, log_level=logging.ERROR)
        return

    try:
        future = asyncio.run_coroutine_threadsafe(coro, loop)
    except RuntimeError:
        coro.close()
        raise RuntimeError(f"Skipping {description}: event loop is closed.") from None

    if timeout is not None and timeout <= 0:
        return

    try:
        return future.result(timeout)
    except concurrent.futures.TimeoutError:
        raise FSTimeoutError(
            f"{description} did not complete within {timeout}s."
        ) from None


def sync_wrapper(func, obj=None):
    """Given a function, make so can be called in blocking contexts

    Leave obj=None if defining within a class. Pass the instance if attaching
    as an attribute of the instance.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        self = obj or args[0]
        return sync(self.loop, func, *args, **kwargs)

    return wrapper


def async_gen_wrapper(func, obj=None):
    """Given a async generator, make so can be called in blocking contexts"""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        self = obj or args[0]
        gen = func(*args, **kwargs)
        while True:
            try:
                yield sync(self.loop, gen.__anext__)
            except StopAsyncIteration:
                break

    return wrapper


def get_loop():
    """Create or return the default fsspec IO loop

    The loop will be running on a separate thread.
    """
    if loop[0] is None:
        with get_lock():
            # repeat the check just in case the loop got filled between the
            # previous two calls from another thread
            if loop[0] is None:
                loop[0] = asyncio.new_event_loop()
                th = threading.Thread(target=loop[0].run_forever, name="fsspecIO")
                th.daemon = True
                th.start()
                iothread[0] = th
    return loop[0]


def reset_after_fork():
    global lock
    loop[0] = None
    iothread[0] = None
    lock = None


if hasattr(os, "register_at_fork"):
    # should be posix; this will do nothing for spawn or forkserver subprocesses
    os.register_at_fork(after_in_child=reset_after_fork)


if TYPE_CHECKING:
    import resource

    ResourceError = resource.error
else:
    try:
        import resource
    except ImportError:
        resource = None
        ResourceError = OSError
    else:
        ResourceError = getattr(resource, "error", OSError)

_DEFAULT_BATCH_SIZE = 128
_NOFILES_DEFAULT_BATCH_SIZE = 1280


def _get_batch_size(nofiles=False):
    from fsspec.config import conf

    if nofiles:
        if "nofiles_gather_batch_size" in conf:
            return conf["nofiles_gather_batch_size"]
    else:
        if "gather_batch_size" in conf:
            return conf["gather_batch_size"]
    if nofiles:
        return _NOFILES_DEFAULT_BATCH_SIZE
    if resource is None:
        return _DEFAULT_BATCH_SIZE

    try:
        soft_limit, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
    except (ImportError, ValueError, ResourceError):
        return _DEFAULT_BATCH_SIZE

    if soft_limit == resource.RLIM_INFINITY:
        return -1
    else:
        return soft_limit // 8


def running_async() -> bool:
    """Being executed by an event loop?"""
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


async def _run_coros_in_chunks(
    coros,
    batch_size=None,
    callback=DEFAULT_CALLBACK,
    timeout=None,
    return_exceptions=False,
    nofiles=False,
):
    """Run the given coroutines in  chunks.

    Parameters
    ----------
    coros: list of coroutines to run
    batch_size: int or None
        Number of coroutines to submit/wait on simultaneously.
        If -1, then it will not be any throttling. If
        None, it will be inferred from _get_batch_size()
    callback: fsspec.callbacks.Callback instance
        Gets a relative_update when each coroutine completes
    timeout: number or None
        If given, each coroutine times out after this time. Note that, since
        there are multiple batches, the total run time of this function will in
        general be longer
    return_exceptions: bool
        Same meaning as in asyncio.gather
    nofiles: bool
        If inferring the batch_size, does this operation involve local files?
        If yes, you normally expect smaller batches.
    """

    if batch_size is None:
        batch_size = _get_batch_size(nofiles=nofiles)

    if batch_size == -1:
        batch_size = len(coros)
    elif batch_size <= 0:
        raise ValueError

    async def _run_coro(coro, i):
        try:
            return await asyncio.wait_for(coro, timeout=timeout), i
        except Exception as e:
            if not return_exceptions:
                raise
            return e, i
        finally:
            callback.relative_update(1)

    i = 0
    n = len(coros)
    results = [None] * n
    pending = set()

    while pending or i < n:
        while len(pending) < batch_size and i < n:
            pending.add(asyncio.ensure_future(_run_coro(coros[i], i)))
            i += 1

        if not pending:
            break

        done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        first_exc = None
        while done:
            task = done.pop()
            try:
                result, k = await task
                results[k] = result
            except Exception as exc:
                if first_exc is None:
                    first_exc = exc

        if first_exc is not None:
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            raise first_exc

    return results


# these methods should be implemented as async by any async-able backend
async_methods = [
    "_ls",
    "_cat_file",
    "_get_file",
    "_put_file",
    "_rm_file",
    "_cp_file",
    "_pipe_file",
    "_expand_path",
    "_info",
    "_isfile",
    "_isdir",
    "_exists",
    "_walk",
    "_glob",
    "_find",
    "_du",
    "_size",
    "_mkdir",
    "_makedirs",
]


class AsyncFileSystem(AbstractFileSystem):
    """Async file operations, default implementations

    Passes bulk operations to asyncio.gather for concurrent operation.

    Implementations that have concurrent batch operations and/or async methods
    should inherit from this class instead of AbstractFileSystem. Docstrings are
    copied from the un-underscored method in AbstractFileSystem, if not given.
    """

    # note that methods do not have docstring here; they will be copied
    # for _* methods and inferred for overridden methods.

    async_impl = True
    mirror_sync_methods = True
    disable_throttling = False

    def __init__(self, *args, asynchronous=False, loop=None, batch_size=None, **kwargs):
        self.asynchronous = asynchronous
        self._pid = os.getpid()
        if not asynchronous:
            self._loop = loop or get_loop()
        else:
            self._loop = None
        self.batch_size = batch_size
        super().__init__(*args, **kwargs)

    @property
    def loop(self):
        if self._pid != os.getpid():
            raise RuntimeError("This class is not fork-safe")
        return self._loop

    async def _rm_file(self, path, **kwargs):
        if (
            inspect.iscoroutinefunction(self._rm)
            and type(self)._rm is not AsyncFileSystem._rm
        ):
            return await self._rm(path, recursive=False, batch_size=1, **kwargs)
        raise NotImplementedError

    async def _rm(
        self, path, recursive=False, batch_size=None, maxdepth=None, **kwargs
    ):
        # TODO: implement on_error
        batch_size = batch_size or self.batch_size
        path = await self._expand_path(path, recursive=recursive, maxdepth=maxdepth)
        return await _run_coros_in_chunks(
            [self._rm_file(p, **kwargs) for p in reversed(path)],
            batch_size=batch_size,
            nofiles=True,
        )

    async def _cp_file(self, path1, path2, **kwargs):
        raise NotImplementedError

    async def _mv_file(self, path1, path2):
        await self._cp_file(path1, path2)
        await self._rm_file(path1)

    async def _copy(
        self,
        path1,
        path2,
        recursive=False,
        on_error=None,
        maxdepth=None,
        batch_size=None,
        **kwargs,
    ):
        if on_error is None and recursive:
            on_error = "ignore"
        elif on_error is None:
            on_error = "raise"

        if isinstance(path1, list) and isinstance(path2, list):
            # No need to expand paths when both source and destination
            # are provided as lists
            paths1 = path1
            paths2 = path2
        else:
            source_is_str = isinstance(path1, str)
            paths1 = await self._expand_path(
                path1, maxdepth=maxdepth, recursive=recursive
            )
            if source_is_str and (not recursive or maxdepth is not None):
                # Non-recursive glob does not copy directories
                paths1 = [
                    p for p in paths1 if not (trailing_sep(p) or await self._isdir(p))
                ]
                if not paths1:
                    return

            source_is_file = len(paths1) == 1
            dest_is_dir = isinstance(path2, str) and (
                trailing_sep(path2) or await self._isdir(path2)
            )

            exists = source_is_str and (
                (has_magic(path1) and source_is_file)
                or (not has_magic(path1) and dest_is_dir and not trailing_sep(path1))
            )
            paths2 = other_paths(
                paths1,
                path2,
                exists=exists,
                flatten=not source_is_str,
            )

        batch_size = batch_size or self.batch_size
        coros = [self._cp_file(p1, p2, **kwargs) for p1, p2 in zip(paths1, paths2)]
        result = await _run_coros_in_chunks(
            coros, batch_size=batch_size, return_exceptions=True, nofiles=True
        )

        for ex in filter(is_exception, result):
            if on_error == "ignore" and isinstance(ex, FileNotFoundError):
                continue
            raise ex

    async def _pipe_file(self, path, value, mode="overwrite", **kwargs):
        raise NotImplementedError

    async def _pipe(self, path, value=None, batch_size=None, **kwargs):
        if isinstance(path, str):
            path = {path: value}
        batch_size = batch_size or self.batch_size
        return await _run_coros_in_chunks(
            [self._pipe_file(k, v, **kwargs) for k, v in path.items()],
            batch_size=batch_size,
            nofiles=True,
        )

    async def _process_limits(self, url, start, end):
        """Helper for "Range"-based _cat_file"""
        size = None
        suff = False
        if start is not None and start < 0:
