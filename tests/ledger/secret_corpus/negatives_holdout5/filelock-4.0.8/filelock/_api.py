from __future__ import annotations

import contextlib
import inspect
import logging
import math
import os
import secrets
import stat
import sys
import time
import warnings
from abc import ABCMeta, abstractmethod
from collections.abc import Callable, Hashable
from contextlib import contextmanager
from dataclasses import dataclass
from itertools import count, starmap
from threading import Condition, RLock, get_ident, local
from typing import TYPE_CHECKING, Final, Literal, NoReturn, TypedDict, TypeVar, cast
from weakref import WeakKeyDictionary, WeakValueDictionary

from ._error import SoftFileLockLifetimeWarning, Timeout
from ._util import break_lock_file

#: No explicit file permission mode was passed. Lock files then open with 0o666 so umask and default ACLs pick
#: the final permissions, and fchmod is skipped to preserve POSIX default ACL inheritance.
_UNSET_FILE_MODE: Final[int] = -1

#: Ceiling on the retry counter used as a power of two, so a long contended wait cannot overflow the backoff multiply.
_MAX_BACKOFF_EXPONENT: Final[int] = 20

#: How a context manager reconciles a body failure with a release failure on exit (see the property of this name).
ContextErrorPolicy = Literal["chain", "group"]
_CONTEXT_ERROR_POLICIES: Final[frozenset[str]] = frozenset({"chain", "group"})

#: What a descriptor-owning backend does with an ``os.close`` failure after relinquishing ownership (see the property).
CloseErrorPolicy = Literal["default", "raise", "suppress"]
_CLOSE_ERROR_POLICIES: Final[frozenset[str]] = frozenset({"default", "raise", "suppress"})

if TYPE_CHECKING:
    from collections.abc import Generator
    from types import TracebackType
    from typing import Protocol

    from _typeshed import Unused

    from ._read_write import ReadWriteLock
    from ._soft_rw import SoftReadWriteLock

    class _ForkResettable(Protocol):
        def _reset_after_fork_in_child(self) -> None: ...

    class _ForkDescriptorOwner(Protocol):
        def _descriptors_for_fork(self) -> tuple[tuple[int, tuple[int, int] | None], ...]: ...

    # Matched against the class object itself rather than `type[...]` of it. A metaclass supplies this method to the
    # class while leaving instances without it, so a `type[_ForkResettableClass]` bound rejects `ReadWriteLock`.
    class _ForkResettableClass(Protocol):
        def _reset_class_after_fork(self) -> None: ...

    class _RegisterAtFork(Protocol):
        def __call__(
            self,
            *,
            before: Callable[[], None] | None = None,
            after_in_parent: Callable[[], None] | None = None,
            after_in_child: Callable[[], None] | None = None,
        ) -> None: ...

    if sys.version_info >= (3, 11):  # pragma: no cover (py311+)
        from typing import Self
    else:  # pragma: no cover (<py311)
        from typing_extensions import Self

_LOGGER: Final[logging.Logger] = logging.getLogger("filelock")
_REGISTER_AT_FORK: Final[_RegisterAtFork | None] = cast("_RegisterAtFork | None", getattr(os, "register_at_fork", None))
_HAS_REGISTER_AT_FORK: Final[bool] = _REGISTER_AT_FORK is not None

_ExtraValue = TypeVar("_ExtraValue")
_MarkerValue = TypeVar("_MarkerValue")
_SubclassValue = TypeVar("_SubclassValue")
_LockInitValue = float | int | bool | str | None | Callable[[int], None]


class LockOptions(TypedDict, total=False):
    """Every option the metaclass forwards, so a subclass adding its own can still type what it passes through."""

    timeout: float
    mode: int
    thread_local: bool
    blocking: bool
    is_singleton: bool
    poll_interval: float
    lifetime: float | None
    context_error_policy: ContextErrorPolicy
    close_error_policy: CloseErrorPolicy
    fallback_to_soft: bool
    preserve_lock_file: bool
    on_acquired: Callable[[int], None] | None


def _exception_group_cls() -> type[BaseException]:
    # BaseExceptionGroup is a builtin on 3.11+; on 3.10 it needs the exceptiongroup backport. filelock keeps zero
    # runtime dependencies, so the backport is imported lazily rather than required, and only group mode needs it.
    if sys.version_info >= (3, 11):  # pragma: no cover (py311+)
        return BaseExceptionGroup  # ruff:ignore[undefined-name]  # builtin on 3.11+
    # Alias the import so BaseExceptionGroup above stays the builtin rather than an unbound local of this function.
    from exceptiongroup import (  # ruff:ignore[import-outside-top-level]  # pragma: no cover (<py311)
        BaseExceptionGroup as _Backport,
    )

    return _Backport  # pragma: no cover (<py311)


def _raise_grouped_errors(
    message: str,
    first_error: BaseException,
    second_error: BaseException,
    *additional_errors: BaseException,
    marker: tuple[str, _MarkerValue] | None = None,
) -> NoReturn:
    errors = (first_error, second_error, *additional_errors)
    _detach_grouped_contexts(errors)
    group = _exception_group_cls()(message, errors)
    if marker is not None:
        setattr(group, marker[0], marker[1])
    raise group from None


def _detach_grouped_contexts(errors: tuple[BaseException, ...]) -> None:
    seen: set[int] = set()
    pending = list(errors)
    while pending:
        error = pending.pop()
        if id(error) in seen:
            continue
        seen.add(id(error))
        if (context := error.__context__) is not None and (
            context is error
            or _same_exception_tree(error, context)
            or any(context is root or _contains_exception(root, context) for root in errors)
        ):
            error.__context__ = None
        elif context is not None:
            pending.append(context)
        if error.__cause__ is not None:
            pending.append(error.__cause__)
        if isinstance(error, _exception_group_cls()):
            pending.extend(cast("_ExceptionGroupProtocol", error).exceptions)


def _same_exception_tree(first: BaseException, second: BaseException) -> bool:
    pending = [(first, second)]
    seen: set[tuple[int, int]] = set()
    while pending:
        first_error, second_error = pending.pop()
        if first_error is second_error:
            continue
        if (pair := (id(first_error), id(second_error))) in seen:
            continue
        seen.add(pair)
        if (
            type(first_error) is not type(second_error)
            or not isinstance(first_error, _exception_group_cls())
            or not isinstance(second_error, _exception_group_cls())
        ):
            return False
        first_group = cast("_ExceptionGroupProtocol", first_error)
        second_group = cast("_ExceptionGroupProtocol", second_error)
        if first_group.message != second_group.message or len(first_group.exceptions) != len(second_group.exceptions):
            return False
        pending.extend(zip(first_group.exceptions, second_group.exceptions, strict=True))
    return True


def _contains_exception(error: BaseException, target: BaseException | None) -> bool:
    if target is None or not isinstance(error, _exception_group_cls()):
        return False
    pending = list(cast("_ExceptionGroupProtocol", error).exceptions)
    seen: set[int] = set()
    while pending:
        child = pending.pop()
        if child is target:
            return True
        if id(child) in seen:
            continue
        seen.add(id(child))
        if isinstance(child, _exception_group_cls()):
            pending.extend(cast("_ExceptionGroupProtocol", child).exceptions)
    return False


def _append_exception_context(error: BaseException, context: BaseException) -> None:
    if _exception_graph_contains(error, context) or _exception_graph_contains(context, error):
        return
    if error.__context__ is None:
        error.__context__ = context
        return
    tail = error
    seen: set[int] = set()
    while id(tail) not in seen:
        seen.add(id(tail))
        if (next_error := tail.__cause__ if tail.__cause__ is not None else tail.__context__) is None:
            tail.__context__ = context
            return
        tail = next_error


def _exception_graph_contains(error: BaseException, target: BaseException) -> bool:
    pending = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if current is target:
            return True
        if id(current) in seen:  # pragma: no cover - arbitrary caller exceptions can contain cycles
            continue
        seen.add(id(current))
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
        if isinstance(current, _exception_group_cls()):
            pending.extend(cast("_ExceptionGroupProtocol", current).exceptions)
    return False


def _grouped_errors(
    error: BaseException, message: str, marker: tuple[str, _MarkerValue]
) -> tuple[BaseException, ...] | None:
    if not isinstance(error, _exception_group_cls()):
        return None
    group = cast("_ExceptionGroupProtocol", error)
    return group.exceptions if group.message == message and getattr(group, marker[0], None) is marker[1] else None


if TYPE_CHECKING:

    class _ExceptionGroupProtocol(Protocol):
        @property
        def message(self) -> str: ...

        @property
        def exceptions(self) -> tuple[BaseException, ...]: ...


def _raise_chained_errors(first_error: BaseException, second_error: BaseException | None = None) -> NoReturn:
    if second_error is None:
        first_context = first_error.__context__
        try:
            raise first_error  # ruff:ignore[raise-within-try]  # the handler restores caller-supplied context before propagation
        except BaseException:
            first_error.__context__ = first_context
            raise
    if (second_context := second_error.__context__) is not None and second_context is not first_error:
        _detach_exception_context(second_context, first_error)
        _append_exception_context(first_error, second_context)
    first_context = first_error.__context__
    try:
        raise first_error  # ruff:ignore[raise-within-try]  # the second raise needs this error as implicit context
    except BaseException:  # ruff:ignore[blind-except]  # first_error may be a control-flow exception
        first_error.__context__ = first_context
        try:
            raise second_error  # ruff:ignore[raise-within-try]  # the handler makes the chain interpreter-independent
        except BaseException:
            second_error.__context__ = first_error
            first_error.__context__ = first_context
            raise


def _detach_exception_context(error: BaseException, target: BaseException) -> None:
    pending = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if current.__context__ is target:
            current.__context__ = None
        elif current.__context__ is not None:
            pending.append(current.__context__)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if isinstance(current, _exception_group_cls()):
            pending.extend(cast("_ExceptionGroupProtocol", current).exceptions)


def _raise_body_and_release(body_error: BaseException, release_error: BaseException) -> NoReturn:
    # Group mode: surface the body failure and the release failure as sibling leaves instead of letting one hide in the
    # other's __context__. BaseExceptionGroup returns a plain ExceptionGroup when both leaves subclass Exception, so
    # ``except*`` and ``except Exception`` still catch them; a BaseException leaf (KeyboardInterrupt, CancelledError)
    # keeps the group outside ordinary handlers. ``from None`` stops the group itself gaining a redundant __context__.
    _raise_grouped_errors("lock body and release both failed", body_error, release_error)


def _raise_cleanup_errors(
    message: str,
    primary_error: BaseException,
    *cleanup_errors: BaseException | None,
) -> NoReturn:
    _raise_grouped_errors(
        message,
        primary_error,
        *(error for error in cleanup_errors if error is not None),
    )


# On Windows os.path.realpath calls CreateFileW with share_mode=0, which blocks concurrent DeleteFileW and causes
# livelocks under threaded contention with SoftFileLock. os.path.abspath is purely string-based and avoids this.
_resolve_dir: Final[Callable[[str], str]] = os.path.abspath if sys.platform == "win32" else os.path.realpath


def _canonical(path: str | os.PathLike[str]) -> str:
    """
    Return one stable key for *path*, collapsing equivalent spellings without following a final symlink.

    Relative, absolute, and ``./`` spellings of one lock file must map to a single singleton instance, deadlock-registry
    entry, and removal key. Resolving the whole path with ``realpath`` would follow a final symlink and alias a lock
    target the backend deliberately rejects, so the registry identity would differ from the backend's. Resolving only
    the parent directory and re-appending the literal final component collapses the equivalent spellings while keeping a
    final symlink a distinct key. On Windows the parent is resolved with ``abspath`` so junctions and reparse points are
    not followed either.
    """
    parent, name = os.path.split(os.fspath(path))
    return os.path.join(_resolve_dir(parent or os.curdir), name)  # ruff:ignore[os-path-join]  # string join matches abspath/realpath


class _ThreadLocalRegistry(local):
    def __init__(self) -> None:
        super().__init__()
        self.held: dict[Hashable, int] = {}


_registry: Final[_ThreadLocalRegistry] = _ThreadLocalRegistry()


_T = TypeVar("_T", bound="BaseFileLock")


class FileLockMeta(ABCMeta):
    _instances: WeakValueDictionary[str, BaseFileLock]
    _instances_lock: RLock
    _instances_under_construction: set[str]

    def __call__(  # ruff:ignore[too-many-arguments]  # forwards the public constructor's documented parameters
        cls: type[_T],
        lock_file: str | os.PathLike[str],
        timeout: float = -1,
        mode: int = _UNSET_FILE_MODE,
        thread_local: bool = True,  # ruff:ignore[boolean-type-hint-positional-argument, boolean-default-value-positional-argument]  # public API: positional bool kept for backwards compatibility
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
        **kwargs: _ExtraValue,
    ) -> _T:
        _ensure_current_process()
        lifetime = _resolve_lifetime(lifetime, cls, stacklevel=cls._constructor_lifetime_warning_stacklevel)
        poll_interval = _resolve_poll_interval(poll_interval)
        # Validate before building the instance: a raise inside __init__ would leave a half-constructed object whose
        # __del__ then trips over the missing context.
        # A lock reopens, reads, or deletes the files it creates, so a mode that denies the owner read or write fails
        # later and for good.
        if mode != _UNSET_FILE_MODE and ~mode & (stat.S_IRUSR | stat.S_IWUSR):
            msg = f"mode={mode:#o} must grant the owner read and write for {cls.__name__}"
            raise ValueError(msg)
        context_error_policy = _resolve_context_error_policy(context_error_policy)
        close_error_policy = _resolve_close_error_policy(close_error_policy)
        preserve_lock_file = _resolve_preserve_lock_file(
            preserve=preserve_lock_file, supported=cls._preserve_lock_file_supported, cls_name=cls.__name__
        )
        on_acquired = _resolve_on_acquired(on_acquired, supported=cls._on_acquired_supported, cls_name=cls.__name__)
        params: dict[str, _LockInitValue | _ExtraValue] = {
            "timeout": timeout,
            "mode": mode,
            "thread_local": thread_local,
            "blocking": blocking,
            "is_singleton": is_singleton,
            "poll_interval": poll_interval,
            "lifetime": lifetime,
