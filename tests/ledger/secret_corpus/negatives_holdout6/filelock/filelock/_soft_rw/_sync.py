"""Cross-process and cross-host reader/writer lock over a generation log of immutable snapshots."""

from __future__ import annotations

import atexit
import os
import threading
import time
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import TYPE_CHECKING, Final
from weakref import WeakValueDictionary

from filelock._api import (
    AcquireReturnProxy,
    _canonical,
    _ensure_current_process,
    _register_fork_class,
    _register_fork_object,
)
from filelock._error import Timeout
from filelock._lease import LeaseCompromise

from ._protocol import GenerationLog, Ledger, Mode, Participant
from ._storage import OsFiles

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

    from filelock._lease import CompromiseReason

_ALL_INSTANCES: Final[WeakValueDictionary[int, SoftReadWriteLock]] = WeakValueDictionary()
_ALL_INSTANCES_LOCK: threading.Lock = threading.Lock()
_SINGLETONS_UNDER_CONSTRUCTION: Final[set[Path]] = set()


class _SoftRWMeta(type):
    _instances: WeakValueDictionary[Path, SoftReadWriteLock]
    _instances_lock: threading.RLock

    def __call__(  # ruff:ignore[too-many-arguments]  # forwards the public constructor's documented parameters
        cls,
        lock_file: str | os.PathLike[str],
        timeout: float = -1,
        *,
        blocking: bool = True,
        is_singleton: bool = True,
        heartbeat_interval: float = 30.0,
        stale_threshold: float | None = None,
        poll_interval: float = 0.25,
        on_compromise: Callable[[LeaseCompromise], None] | None = None,
    ) -> SoftReadWriteLock:
        _ensure_current_process()
        # Passed through only when set, so a subclass that declares its own constructor without it keeps working.
        extra = {} if on_compromise is None else {"on_compromise": on_compromise}
        if not is_singleton:
            return super().__call__(
                lock_file,
                timeout,
                blocking=blocking,
                is_singleton=is_singleton,
                heartbeat_interval=heartbeat_interval,
                stale_threshold=stale_threshold,
                poll_interval=poll_interval,
                **extra,
            )

        normalized = Path(lock_file).resolve()
        with cls._instances_lock:
            instance = cls._instances.get(normalized)
            if instance is None:
                if normalized in _SINGLETONS_UNDER_CONSTRUCTION:  # pragma: needs fork
                    msg = f"Singleton lock construction is already active for {lock_file!s}"
                    raise RuntimeError(msg)
                construction_pid = os.getpid()
                _SINGLETONS_UNDER_CONSTRUCTION.add(normalized)
                try:
                    instance = super().__call__(
                        lock_file,
                        timeout,
                        blocking=blocking,
                        is_singleton=is_singleton,
                        heartbeat_interval=heartbeat_interval,
                        stale_threshold=stale_threshold,
                        poll_interval=poll_interval,
                        **extra,
                    )
                finally:
                    _SINGLETONS_UNDER_CONSTRUCTION.discard(normalized)
                if os.getpid() != construction_pid:  # pragma: needs fork
                    msg = "Lock construction cannot continue after fork; construct a new lock in the child"
                    raise RuntimeError(msg)
                cls._instances[normalized] = instance
            elif instance.timeout != timeout or instance.blocking != blocking:
                msg = (
                    f"Singleton lock created with timeout={instance.timeout}, blocking={instance.blocking},"
                    f" cannot be changed to timeout={timeout}, blocking={blocking}"
                )
                raise ValueError(msg)
            elif on_compromise is not None and instance._on_compromise != on_compromise:  # ruff: ignore[private-member-access]  # the metaclass owns the singleton it compares
                # A tuning difference keeps the first caller's values; a different loss callback is a different safety
                # contract, and returning the cached instance would silently drop it.
                msg = f"Singleton lock created with a different on_compromise callback for {lock_file!s}"
                raise ValueError(msg)
            else:
                _validate_intervals(heartbeat_interval, stale_threshold, poll_interval)
            return instance


class SoftReadWriteLock(metaclass=_SoftRWMeta):
    """
    Cross-process and cross-host reader/writer lock for shared filesystems.

    Use this class instead of :class:`~filelock.ReadWriteLock` when the lock file lives on a network filesystem (NFS,
    Lustre, HPC cluster shared storage). ``ReadWriteLock`` is backed by SQLite and cannot run on NFS because SQLite's
    ``fcntl`` locking is unreliable there.

    The lock's state is a log of immutable snapshots under ``foo.lock.rw/gen/<N>``, each naming the writer and the
    readers holding the lock at generation ``N``. Every acquire, release, and eviction publishes the next snapshot with
    one atomic no-replace hard link, so no transition can be left half done by a crash on any host. Each participant
    also keeps ``foo.lock.rw/holders/<token>``, a record whose nonce a daemon heartbeat thread rewrites every
    ``heartbeat_interval`` seconds. A contender evicts a member whose record has not changed for ``stale_threshold``
    seconds of the contender's own monotonic clock; no clock is ever compared across hosts, so skew between hosts or
    against the file server cannot make a live holder look dead.

    Writer acquire is writer-preferring: a writer enters the snapshot as soon as no live writer is named, which blocks
    any new reader, then waits for the named readers to leave. Writer starvation is impossible.

    An expired holder is not fenced by the lock: a process paused past ``stale_threshold`` resumes believing it holds
    the lock until its next heartbeat reports the loss through ``on_compromise``. :attr:`generation` is a monotonic
    fencing token for the protected resource: a store that rejects writes carrying a lower generation than the highest
    it has accepted refuses such a holder.

    Reentrancy, upgrade/downgrade rules, thread pinning, and singleton caching by resolved path match
    :class:`~filelock.ReadWriteLock`.

    Forking invalidates the inherited instance in the child so the child cannot double-own the lock with its parent;
    ``release()`` on that instance is a no-op, and the child must construct a new instance if it needs a lock.

    Trust boundary: protects against same-UID non-cooperating processes (one host or cross-host) and same-host
    different-UID users via ``0o600`` / ``0o700`` permissions. Does not protect against root compromise or multi-tenant
    mounts where hostile co-tenants share the UID.

    :param lock_file: path to the lock file; the protocol directory lives next to it as ``<lock_file>.rw``
    :param timeout: maximum wait time in seconds; ``-1`` means block indefinitely
    :param blocking: if ``False``, raise :class:`~filelock.Timeout` immediately on contention
    :param is_singleton: if ``True``, reuse existing instances for the same resolved path
    :param heartbeat_interval: seconds between heartbeat refreshes; default 30 s
    :param stale_threshold: seconds a holder record may stay unchanged before a contender evicts it; defaults to
        ``3 * heartbeat_interval``, matching etcd's ``LeaseKeepAlive`` convention
    :param poll_interval: seconds between acquire retries under contention; default 0.25 s
    :param on_compromise: called from the heartbeat thread with a :class:`~filelock.LeaseCompromise` when the hold is
        lost: a peer evicted it, or refreshes failed for long enough that a peer could have

    .. versionadded:: 3.27.0

    .. versionchanged:: 3.33.0

        The on-disk protocol is a generation log; earlier releases cannot share a lock path with this one.

    """

    _instances: WeakValueDictionary[Path, SoftReadWriteLock] = WeakValueDictionary()
    _instances_lock = threading.RLock()

    def __init__(  # ruff:ignore[too-many-arguments]  # public constructor: one parameter per documented lock option
        self,
        lock_file: str | os.PathLike[str],
        timeout: float = -1,
        *,
        blocking: bool = True,
        is_singleton: bool = True,  # ruff:ignore[unused-method-argument]  # consumed by _SoftRWMeta.__call__
        heartbeat_interval: float = 30.0,
        stale_threshold: float | None = None,
        poll_interval: float = 0.25,
        on_compromise: Callable[[LeaseCompromise], None] | None = None,
    ) -> None:
        self._creator_pid = os.getpid()
        stale_threshold = _validate_intervals(heartbeat_interval, stale_threshold, poll_interval)

        self.lock_file: str = os.fspath(lock_file)
        self.timeout: float = timeout
        self.blocking: bool = blocking
        self.heartbeat_interval: float = heartbeat_interval
        self.stale_threshold: float = stale_threshold
        self.poll_interval: float = poll_interval

        # Resolved once: a relative lock path must keep naming the same log after the process changes directory.
        self._root = f"{_canonical(self.lock_file)}.rw"
        self._files = OsFiles(self.lock_file)
        self._ledger = Ledger(time.monotonic)
        self._log = GenerationLog(self._files, self.lock_file, self._root)
        self._on_compromise = on_compromise
        self._locks = _Locks(internal=threading.Lock(), transaction=threading.Lock())
        self._hold: _Hold | None = None
        self._compromise: LeaseCompromise | None = None
        self._closed: bool = False

        with _ALL_INSTANCES_LOCK:
            _ALL_INSTANCES[id(self)] = self
        _register_fork_object(self)

    @classmethod
    def _reset_class_after_fork(cls) -> None:  # pragma: forked child
        global _ALL_INSTANCES_LOCK  # ruff:ignore[global-statement]  # rebinds the module lock to a fresh one in the fork child
        _ALL_INSTANCES_LOCK = threading.Lock()
        cls._instances = WeakValueDictionary()
        cls._instances_lock = threading.RLock()
        _SINGLETONS_UNDER_CONSTRUCTION.clear()

    @property
    def generation(self) -> int | None:
        """
        The generation at which the current hold was granted, or ``None`` when no lock is held.

        Generations are monotonic across all participants and hosts, so this is a fencing token: pass it to the
        protected resource and have the resource reject any operation carrying a lower generation than the highest it
        has accepted. That refuses a holder that paused past ``stale_threshold``, was evicted, and resumed.

        .. versionadded:: 3.33.0

        """
        with self._locks.internal:
            return None if self._hold is None else self._hold.participant.generation

    @property
    def compromise(self) -> LeaseCompromise | None:
        """
        How the current hold was lost, or ``None`` while it stands.

        Set by the heartbeat thread once a peer has evicted this holder or refreshes have failed for long enough that a
        peer could have. The holder should stop using the protected resource when it is set.

        .. versionadded:: 3.33.0

        """
        with self._locks.internal:
            return self._compromise

    @contextmanager
    def read_lock(self, timeout: float | None = None, *, blocking: bool | None = None) -> Generator[None]:
        """
        Context manager that acquires and releases a shared read lock.

        Falls back to instance defaults for *timeout* and *blocking* when ``None``.

        :param timeout: maximum wait time in seconds, or ``None`` to use the instance default
        :param blocking: if ``False``, raise :class:`~filelock.Timeout` immediately; ``None`` uses the instance default

        :raises RuntimeError: if a write lock is already held on this instance
        :raises Timeout: if the lock cannot be acquired within *timeout* seconds

        """
        self.acquire_read(timeout, blocking=blocking)
        try:
            yield
        finally:
            self.release()

    @contextmanager
    def write_lock(self, timeout: float | None = None, *, blocking: bool | None = None) -> Generator[None]:
        """
        Context manager that acquires and releases an exclusive write lock.

        Falls back to instance defaults for *timeout* and *blocking* when ``None``.

        :param timeout: maximum wait time in seconds, or ``None`` to use the instance default
        :param blocking: if ``False``, raise :class:`~filelock.Timeout` immediately; ``None`` uses the instance default

        :raises RuntimeError: if a read lock is already held, or a write lock is held by a different thread
        :raises Timeout: if the lock cannot be acquired within *timeout* seconds

        """
        self.acquire_write(timeout, blocking=blocking)
        try:
            yield
        finally:
            self.release()

    def acquire_read(self, timeout: float | None = None, *, blocking: bool | None = None) -> AcquireReturnProxy:
        """
        Acquire a shared read lock.

        If this instance already holds a read lock, the lock level is incremented (reentrant). Attempting to acquire a
        read lock while holding a write lock raises :class:`RuntimeError` (downgrade not allowed). On the 0→1
        transition the reader publishes its holder record, enters the next snapshot once no live writer is named, and
        starts a daemon heartbeat thread that rewrites the record's nonce every ``heartbeat_interval`` seconds so peers
        on other hosts do not evict it.

        :param timeout: maximum wait time in seconds, or ``None`` to use the instance default; ``-1`` means block
            indefinitely
        :param blocking: if ``False``, raise :class:`~filelock.Timeout` immediately when the lock is unavailable;
            ``None`` uses the instance default

        :returns: a proxy that can be used as a context manager to release the lock

        :raises RuntimeError: if a write lock is already held on this instance, if this instance was invalidated by
            :func:`os.fork`, or if :meth:`close` was called
        :raises Timeout: if the lock cannot be acquired within *timeout* seconds
        :raises SoftFileLockProtocolError: if a snapshot cannot be read without risking overlap, or the filesystem
            refuses the no-replace hard links the protocol commits with

        """
        return self._acquire("read", timeout, blocking=blocking)

    def acquire_write(self, timeout: float | None = None, *, blocking: bool | None = None) -> AcquireReturnProxy:
        """
        Acquire an exclusive write lock.

        If this instance already holds a write lock from the same thread, the lock level is incremented (reentrant).
        Attempting to acquire a write lock while holding a read lock raises :class:`RuntimeError` (upgrade not
        allowed). Write locks are pinned to the acquiring thread: a different thread trying to re-enter also raises
        :class:`RuntimeError`.

        A writer enters the next snapshot as its writer as soon as no live writer is named, which blocks every new
        reader on every host, then waits for the readers that snapshot names to leave. Writer starvation is impossible:
        new readers see the named writer and wait behind it.

        :param timeout: maximum wait time in seconds, or ``None`` to use the instance default; ``-1`` means block
            indefinitely
        :param blocking: if ``False``, raise :class:`~filelock.Timeout` immediately when the lock is unavailable;
            ``None`` uses the instance default

        :returns: a proxy that can be used as a context manager to release the lock

        :raises RuntimeError: if a read lock is already held, if a write lock is held by a different thread, if this
            instance was invalidated by :func:`os.fork`, or if :meth:`close` was called
        :raises Timeout: if the lock cannot be acquired within *timeout* seconds
