from __future__ import annotations

import contextlib
import errno
import os
import secrets
import stat
import sys
import tempfile
import time
from dataclasses import dataclass
from errno import EACCES, EEXIST, ENOENT, ENOSYS, EPERM, ESTALE, EXDEV
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal, cast

from ._api import BaseFileLock, _canonical, _raise_cleanup_errors
from ._error import SoftFileLockProtocolError
from ._identity import host_name, process_start_token
from ._soft_protocol import STRICT_SOFT_SENTINEL_RECORD
from ._util import ensure_directory_exists, write_all

if TYPE_CHECKING:
    from collections.abc import Iterator

StrictSoftFileClaimState = Literal["intent", "held"]

_CLAIM_STATES: Final[frozenset[str]] = frozenset({"intent", "held"})
_COORDINATION_SUFFIX: Final[str] = ".filelock"
_CLAIM_DIRECTORY_NAME: Final[str] = "claims"
_CLAIM_MAGIC: Final[str] = "filelock-strict-v1"
_CLAIM_RECORD_LIMIT: Final[int] = 1024
_CLAIM_NAME_PART_COUNT: Final[int] = 3
_TOKEN_HEX_LENGTH: Final[int] = 32
_PRIVATE_RECORD_MARKER: Final[str] = ".private-v1-"
_PRIVATE_RECORD_SUFFIX: Final[str] = ".tmp"
_PRIVATE_RECORD_RANDOM_HEX_LENGTH: Final[int] = 32
_PRIVATE_RECORD_GRACE: Final[float] = 2.0
_UNLINK_MAX_RETRIES: Final[int] = 10
#: How long a scan waits out a claim held in Windows' delete-pending state before treating it as unreadable.
_CLAIM_READ_GRACE: Final[float] = 0.5
_CLAIM_READ_RETRY: Final[float] = 0.002
#: Windows opens descriptors in text mode by default, which rewrites newlines and truncates a record at a control byte.
#: The claim and sentinel records are exact binary, so every record descriptor must be binary; POSIX ignores the flag.
_O_BINARY: Final[int] = getattr(os, "O_BINARY", 0)
_LEGACY_SENTINEL: Final[bytes] = STRICT_SOFT_SENTINEL_RECORD.encode()
_WINDOWS_HARD_LINK_UNSUPPORTED: Final[frozenset[int]] = frozenset({1, 17, 50})

# Termux/Android CPython ships without os.link (bionic long had only linkat), so the strict backend's whole hard-link
# mechanism is absent there. Probe once and gate every os.link reference on it, so importing filelock still works and
# only an actual StrictSoftFileLock acquire reports the missing capability.
_HAS_LINK: Final[bool] = hasattr(os, "link")

# Probe dir_fd capability once at import. A per-call ``os.unlink in os.supports_dir_fd`` check flips to False the moment
# a test mocks os.unlink, silently diverting the code to a different branch than the one under test.
_OPEN_SUPPORTS_DIR_FD: Final[bool] = os.open in os.supports_dir_fd
_UNLINK_SUPPORTS_DIR_FD: Final[bool] = os.unlink in os.supports_dir_fd
_STAT_SUPPORTS_DIR_FD: Final[bool] = os.stat in os.supports_dir_fd
_LINK_SUPPORTS_DIR_FD: Final[bool] = _HAS_LINK and os.link in os.supports_dir_fd


def _probe_link_follow_symlinks() -> bool:
    # os.supports_follow_symlinks lists os.link on PyPy, but its linkat then rejects follow_symlinks=False with EINVAL,
    # and Windows raises NotImplementedError for the option outright. Link a throwaway file for real so the answer
    # reflects the runtime rather than its advertisement, and treat any failure as "not honored": the option only
    # hardens a source this process created with O_EXCL, so skipping it is safe, and a real environment fault surfaces
    # when the actual link runs.
    if not _HAS_LINK:
        return False
    try:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "probe-source")
            source.touch()
            os.link(source, Path(directory, "probe-link"), follow_symlinks=False)
    except (OSError, NotImplementedError, ValueError):
        return False
    return True


_LINK_HONORS_FOLLOW_SYMLINKS: Final[bool] = _probe_link_follow_symlinks()


def _probe_hard_link_unsupported_errnos() -> frozenset[int]:
    # GraalPy's errno omits ENOTSUP, so importing the name outright breaks every runtime that ships without it. ENOTSUP
    # wins wherever it exists, leaving every runtime that names it unchanged. EOPNOTSUPP only stands in for the ones
    # that do not, and it approximates rather than matches: the two codes agree on Linux but differ on macOS/BSD and on
    # Windows. Where neither exists a runtime that cannot name "operation not supported" cannot raise it either, and
    # ENOSYS/EXDEV still classify the link failures it can raise.
    not_supported = getattr(errno, "ENOTSUP", getattr(errno, "EOPNOTSUPP", None))
    return frozenset({ENOSYS, EXDEV} if not_supported is None else {ENOSYS, EXDEV, not_supported})


_HARD_LINK_UNSUPPORTED_ERRNOS: Final[frozenset[int]] = _probe_hard_link_unsupported_errnos()


class StrictSoftFileLock(BaseFileLock):
    """Portable fail-closed lock based on immutable owner claims."""

    _preserve_lock_file_supported: bool = True
    _on_acquired_supported: bool = False
    #: Age cannot clear a strict claim: expiring one on a clock is the overlap the fail-closed contract exists to rule
    #: out, so only force_break() removes it.
    _lifetime_supported: bool = False
    _lifetime_unsupported_reason: str = "a strict claim is never broken by age, only by force_break()"
    #: Contending processes each publish and rescan several files, so back their retries off across a jittered window
    #: rather than let them collide on every poll. Seconds; keeps a waiter responsive once it wins.
    _poll_backoff_cap: float = 0.05

    def _acquire(self) -> None:
        # Resolve once per acquisition, not per poll: a waiter on a relative path must keep publishing into the
        # directory it started waiting in even when another thread changes the working directory mid-wait.
        if (claim_root := self._context.claim_root) is None:
            claim_root = self._context.claim_root = _canonical(self.lock_file)
        lock_path = Path(claim_root)
        coordination_directory = Path(f"{lock_path}{_COORDINATION_SUFFIX}")
        claim_directory = coordination_directory / _CLAIM_DIRECTORY_NAME
        ensure_directory_exists(os.fspath(lock_path))
        _ensure_protocol_directory(self.lock_file, coordination_directory)
        _ensure_protocol_directory(self.lock_file, claim_directory)
        if (sentinel_fd := _open_or_create_sentinel(self.lock_file, lock_path, self._open_mode())) is None:
            return
        try:
            sentinel_identity = _file_identity(os.fstat(sentinel_fd))
        except BaseException as inspection_error:  # preserve inspection and descriptor cleanup errors
            try:
                os.close(sentinel_fd)
            except BaseException as close_error:  # ruff:ignore[blind-except]  # preserve inspection and descriptor cleanup errors
                _raise_cleanup_errors("strict sentinel inspection cleanup failed", inspection_error, close_error)
            raise
        self._mark_descriptor_pending(sentinel_fd, sentinel_identity)
        try:
            self._attempt_doorway(claim_directory, sentinel_fd, sentinel_identity)
        except BaseException:
            if self._context.pending_lock_file_fd == sentinel_fd:
                self._discard_doorway(sentinel_fd, sentinel_identity)
            raise

    def _attempt_doorway(self, claim_directory: Path, sentinel_fd: int, sentinel_identity: tuple[int, int]) -> None:
        if _read_existing_claims(self.lock_file, claim_directory):
            self._discard_doorway(sentinel_fd, sentinel_identity)
            return

        token = secrets.token_hex(_TOKEN_HEX_LENGTH // 2)
        intent_name = _claim_name("intent", token)
        intent_path = str(claim_directory / intent_name)
        try:
            publication_cleanup_error = _publish_record(intent_path, _claim_record(token), self._open_mode())
        except _PrivateRecordReclaimedError:
            self._discard_doorway(sentinel_fd, sentinel_identity)
            return
        except (NotImplementedError, OSError) as error:
            _raise_if_hard_links_unsupported(self.lock_file, error)
            if isinstance(error, OSError) and error.errno == EEXIST:
                self._discard_doorway(sentinel_fd, sentinel_identity)
                return
            raise
        if publication_cleanup_error is not None:  # pragma: needs dir-fd
            raise publication_cleanup_error
        self._context.owner_claim_paths = (intent_path,)

        claims = _read_existing_claims(self.lock_file, claim_directory)
        if (
            not claims
            or any(claim.state == "held" for claim in claims)
            or min(claim.name for claim in claims) != intent_name
        ):
            self._discard_doorway(sentinel_fd, sentinel_identity)
            return

        held_name = _claim_name("held", token)
        held_path = str(claim_directory / held_name)
        try:
            link_cleanup_error = _link_no_replace(claim_directory, intent_name, held_name)
        except (NotImplementedError, OSError) as error:
            _raise_if_hard_links_unsupported(self.lock_file, error)
            raise
        self._context.owner_claim_paths = (held_path, intent_path)
        if link_cleanup_error is not None:  # pragma: needs dir-fd
            self._context.owner_claim_paths = ()
            raise link_cleanup_error

        claims = _read_existing_claims(self.lock_file, claim_directory)
        if (
            not {intent_name, held_name}.issubset(claim.name for claim in claims)
            or min(_claim_token_key(claim.name) for claim in claims) != f"v1-{token}.claim"
        ):
            self._discard_doorway(sentinel_fd, sentinel_identity)
            return
        # Keep the intent claim for the whole hold rather than unlinking it now. The intent has existed, unchanged,
        # since this owner published it, so a contender's os.scandir is guaranteed to return it (POSIX only leaves the
        # visibility of entries created or removed *during* a scan unspecified). The freshly linked held claim carries
        # no such guarantee: a scan that races its creation can miss it. Were the intent removed here, that scan could
        # observe neither claim and let a larger-token contender win over this owner. The stable intent is the witness
        # that keeps the phase-five min-token decision computed over the true set. Release unlinks both.
        self._mark_descriptor_owned(sentinel_fd, sentinel_identity)

    @property
    def claims(self) -> tuple[StrictSoftFileClaim, ...]:
        """Published claims that block acquisition."""
        return _read_existing_claims(self.lock_file, self._claim_directory)

    def force_break(self, claim_name: str) -> None:
        """Remove one named claim, allowing overlap if its owner still holds the protected resource."""
        _validate_force_break_name(claim_name)
        _require_exact_name(self._claim_directory, claim_name)
        if (
            cleanup_error := _unlink_in_directory(self._claim_directory, claim_name)
        ) is not None:  # pragma: needs dir-fd
            raise cleanup_error

    def _rollback_failed_acquire(self, acquisition_error: BaseException) -> None:
        # _acquire already reconciles a failed doorway through _discard_doorway: it either closes the pending
        # descriptor or, when a held claim cannot be removed, commits it as owned so a later release retries and
        # raises the cleanup errors. A base rollback would release that owned descriptor again and report each
        # failure a second time, so leave the reconciled state alone.
        if self.is_locked:
            return
        super()._rollback_failed_acquire(acquisition_error)

    def _reconcile_failed_acquire(self, canonical: str) -> None:
        # The acquisition is over, so the next one resolves the working directory again rather than reuse this one's.
        if not self.is_locked:
            self._context.claim_root = None
        super()._reconcile_failed_acquire(canonical)

    def _release(self) -> None:
        fd = cast("int", self._context.lock_file_fd)
        self._context.claim_root = None
        remaining, errors = _unlink_owner_paths(self._context.owner_claim_paths)
        self._context.owner_claim_paths = tuple(remaining)
        if remaining:
            _raise_recorded_errors("strict claim release failed", errors)
        self._mark_descriptor_released()
        try:
            self._close_released_fd(fd, default_suppresses=False)
        except BaseException as close_error:  # ruff:ignore[blind-except]  # preserve claim and sentinel cleanup errors
            errors.append(close_error)
        if errors:
            _raise_recorded_errors("strict release cleanup failed", errors)

    def _discard_doorway(self, fd: int, identity: tuple[int, int]) -> None:
        remaining, errors = _unlink_owner_paths(self._context.owner_claim_paths)
        self._context.owner_claim_paths = tuple(remaining)
        if remaining:
            self._mark_descriptor_owned(fd, identity)
            _raise_recorded_errors("strict doorway claim cleanup failed", errors)
        self._mark_descriptor_released()
        try:
            self._close_released_fd(fd, default_suppresses=False)
        except BaseException as close_error:  # ruff:ignore[blind-except]  # preserve claim and sentinel cleanup errors
            errors.append(close_error)
        if errors:
            _raise_recorded_errors("strict doorway cleanup failed", errors)

    @property
    def _claim_directory(self) -> Path:
        if self._context.owner_claim_paths:
            return Path(self._context.owner_claim_paths[0]).parent
        return Path(f"{_canonical(self.lock_file)}{_COORDINATION_SUFFIX}") / _CLAIM_DIRECTORY_NAME


@dataclass(frozen=True)
class StrictSoftFileClaim:
    """One parsed strict soft-lock claim."""

    name: str
    state: StrictSoftFileClaimState
    token: str
    pid: int
    hostname: str
    #: The owner's process start token, or ``None`` when the platform exposes no proven start time. A strict lock never
    #: reclaims a claim on its own, so this identifies the owner for tooling rather than driving any automatic break.
    start: int | None = None


class _PrivateRecordReclaimedError(Exception):
    pass


def _open_or_create_sentinel(lock_file: str, path: Path, mode: int) -> int | None:
    try:
        return _open_sentinel(path)
    except FileNotFoundError:
        pass
    except OSError:
        return None

    _reclaim_sentinel_private_records(path, time.time())
    try:
        publication_cleanup_error = _publish_record(os.fspath(path), _LEGACY_SENTINEL, mode)
    except _PrivateRecordReclaimedError:
        return None
    except (NotImplementedError, OSError) as error:
        _raise_if_hard_links_unsupported(lock_file, error)
        if not isinstance(error, OSError) or error.errno != EEXIST:
            raise
    else:
        if publication_cleanup_error is not None:  # pragma: needs dir-fd
            raise publication_cleanup_error
    try:
        return _open_sentinel(path)
    except OSError:
        return None


def _open_sentinel(path: Path) -> int | None:
    fd, record = _open_record(path, len(_LEGACY_SENTINEL))
    if record == _LEGACY_SENTINEL:
        return fd
    os.close(fd)
    return None


def _read_claims(lock_file: str, directory: Path) -> tuple[StrictSoftFileClaim, ...]:
    claims: list[StrictSoftFileClaim] = []
    for name in _list_claim_names(lock_file, directory):
        if (name_parts := _parse_claim_name(name)) is None:
            raise SoftFileLockProtocolError(lock_file, name, "unknown claim name or protocol version")
        if (record := _read_claim_record(lock_file, directory, name)) is not None:
            claims.append(_parse_claim(lock_file, name, name_parts, record))
    return tuple(claims)


def _list_claim_names(lock_file: str, directory: Path) -> list[str]:
    try:
        with os.scandir(directory) as entries:
            return _public_claim_names(directory, entries)
    except OSError as error:
        reason = f"cannot list claim directory: {error.strerror or type(error).__name__}"
        raise SoftFileLockProtocolError(lock_file, None, reason) from error


def _read_claim_record(lock_file: str, directory: Path, name: str) -> bytes | None:
    # A contended scan can list a claim that is not yet cleanly readable, in two ways that both resolve on a brief
