"""
Functionality: Store encrypted attachment blobs on local disk (the Phase 1 BlobStore implementation).
Owns: the on-disk layout, write-once atomic writes, and ref validation.
Public entry: LocalDiskBlobStore
Decisions: D-0006, D-0013, D-0015
Assumptions: none
Notes: Layout (D-0013): <root>/<first 2 hex of ref>/<64-hex ref>, files 0600. put_if_absent writes a temp file,
  fsyncs it, then hard-links it into place. The link fails if the name exists, so an existing blob is never
  overwritten and concurrent writers of one ref converge on a single file. Blobs are ciphertext: the store never
  sees plaintext or keys. Erasure is key destruction (D-0004). delete() exists only for orphan collection
  (D-0015): the caller holds the exclusive per-ref lock and has confirmed no committed event references the ref.
  Age = now - mtime; a dedup hit does not refresh it, which is why the lock, not the age, is the safety rule.
"""
import os
import secrets
import time
from collections.abc import Iterator
from pathlib import Path

from nacre.core.blob_store import REF_LENGTH


class LocalDiskBlobStore:
    """core.blob_store.BlobStore on a local directory."""

    def __init__(self, root: Path):
        self._root = Path(root)
        self._root.mkdir(mode=0o700, parents=True, exist_ok=True)

    def _path(self, ref: bytes) -> Path:
        if type(ref) is not bytes or len(ref) != REF_LENGTH:
            raise ValueError(f"refs are exactly {REF_LENGTH} bytes")
        h = ref.hex()
        return self._root / h[:2] / h

    def put_if_absent(self, ref: bytes, blob: bytes) -> bool:
        path = self._path(ref)
        if path.exists():
            return False
        path.parent.mkdir(mode=0o700, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, blob)
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            os.link(tmp, path)
            return True
        except FileExistsError:
            return False
        finally:
            tmp.unlink()

    def get(self, ref: bytes) -> bytes:
        try:
            return self._path(ref).read_bytes()
        except FileNotFoundError:
            raise KeyError(ref.hex()) from None

    def exists(self, ref: bytes) -> bool:
        return self._path(ref).exists()

    def list_refs(self) -> Iterator[tuple[bytes, float]]:
        now = time.time()
        for shard in sorted(p for p in self._root.iterdir() if p.is_dir() and len(p.name) == 2):
            for path in sorted(shard.iterdir()):
                if len(path.name) == 2 * REF_LENGTH and not path.name.startswith("."):
                    try:
                        yield bytes.fromhex(path.name), now - path.stat().st_mtime
                    except (ValueError, FileNotFoundError):
                        continue

    def delete(self, ref: bytes) -> bool:
        try:
            self._path(ref).unlink()
            return True
        except FileNotFoundError:
            return False

    def remove_stale_temp(self, min_age_seconds: float) -> int:
        now, removed = time.time(), 0
        for tmp in self._root.glob("*/.*.tmp"):
            try:
                if now - tmp.stat().st_mtime >= min_age_seconds:
                    tmp.unlink()
                    removed += 1
            except FileNotFoundError:
                continue
        return removed
