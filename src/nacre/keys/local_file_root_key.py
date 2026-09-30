"""
Functionality: Root keys held in local files (Phase 1 implementation of RootKeyProvider).
Owns: root-key files and the CURRENT pointer, file-permission checks, AES-256-GCM wrapping of stream
  master keys, version creation and destruction.
Public entry: LocalFileRootKeyProvider
Decisions: D-0004
Assumptions: A-0008
Notes: The directory must live outside the repository and outside the database host's backups
  (D-0004 amendment 4). Layout: one `<version>.key` file (32 random bytes) per version plus a `CURRENT`
  file naming the current version. Every write is atomic (temp file + os.replace), mode 0600.
  Loading refuses any key file readable by group or others. (D1)
  Wrap: nonce(12) | AES-256-GCM(root, master_key), with AAD = "nacre-root-wrap-v1" | version | 0x00 |
  context, which binds a wrapped key to its root version and its stream (context = stream_id bytes).
  Destroy overwrites the file with zeros, fsyncs, then unlinks. This is best effort against the local
  disk. It cannot reach copies elsewhere (snapshots, the separate backup): destroying those is the
  operator step D-0004 amendment 6 requires.
  Version ids match keys.stream_master_keys.root_key_version ('^[A-Za-z0-9._:-]{1,64}$').
"""
import os
import secrets
import stat
from datetime import UTC, datetime
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from nacre.core.root_key_provider import WrappedKey

KEY_BYTES = 32
_NONCE_BYTES = 12
_AAD_PREFIX = b"nacre-root-wrap-v1"


class LocalFileRootKeyProvider:
    """RootKeyProvider backed by 0600 files in one directory."""

    def __init__(self, directory: Path):
        self._dir = Path(directory)
        if not (self._dir / "CURRENT").is_file():
            raise FileNotFoundError(f"no CURRENT root-key pointer in {self._dir}; call create_version() first")

    @classmethod
    def initialise(cls, directory: Path) -> "LocalFileRootKeyProvider":
        """Create the directory (0700) and a first root-key version."""
        directory = Path(directory)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        provider = cls.__new__(cls)
        provider._dir = directory
        provider.create_version()
        return provider

    def current_version(self) -> str:
        return (self._dir / "CURRENT").read_text().strip()

    def wrap(self, master_key: bytes, context: bytes) -> WrappedKey:
        if type(master_key) is not bytes or len(master_key) != KEY_BYTES:
            raise ValueError(f"master keys are exactly {KEY_BYTES} bytes")
        version = self.current_version()
        nonce = secrets.token_bytes(_NONCE_BYTES)
        sealed = AESGCM(self._load(version)).encrypt(nonce, master_key, _aad(version, context))
        return WrappedKey(root_key_version=version, wrapped=nonce + sealed)

    def unwrap(self, wrapped: WrappedKey, context: bytes) -> bytes:
        root = self._load(wrapped.root_key_version)
        blob = wrapped.wrapped
        try:
            return AESGCM(root).decrypt(blob[:_NONCE_BYTES], blob[_NONCE_BYTES:],
                                        _aad(wrapped.root_key_version, context))
        except (InvalidTag, ValueError):
            raise ValueError("wrapped master key failed authentication (tampered or wrong context)") from None

    def create_version(self) -> str:
        version = f"root-{datetime.now(UTC):%Y%m%dT%H%M%S}-{secrets.token_hex(4)}"
        _atomic_write(self._dir / f"{version}.key", secrets.token_bytes(KEY_BYTES))
        _atomic_write(self._dir / "CURRENT", version.encode())
        return version

    def destroy_version(self, version: str) -> None:
        if version == self.current_version():
            raise ValueError("cannot destroy the current root-key version; create a new one first")
        path = self._key_path(version)
        with open(path, "r+b") as f:
            f.write(b"\x00" * KEY_BYTES)
            f.flush()
            os.fsync(f.fileno())
        path.unlink()

    def _key_path(self, version: str) -> Path:
        if not version or "/" in version or version.startswith("."):
            raise KeyError(f"invalid root-key version {version!r}")
        return self._dir / f"{version}.key"

    def _load(self, version: str) -> bytes:
        path = self._key_path(version)
        if not path.is_file():
            raise KeyError(f"root-key version {version!r} is not held (destroyed or never created)")
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise PermissionError(f"{path} is readable by group or others; chmod 600 it")
        key = path.read_bytes()
        if len(key) != KEY_BYTES:
            raise ValueError(f"{path} is not a {KEY_BYTES}-byte key")
        return key


def _aad(version: str, context: bytes) -> bytes:
    return _AAD_PREFIX + version.encode() + b"\x00" + context


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
