"""
Functionality: Store encrypted attachment blobs on local disk (the Phase 1 BlobStore implementation).
Owns: the on-disk layout, write-once atomic writes, and ref validation.
Public entry: LocalDiskBlobStore
Decisions: D-0006, D-0013
Assumptions: none
Notes: Layout (D-0013): <root>/<first 2 hex of ref>/<64-hex ref>, files 0600. put_if_absent writes a temp file,
  fsyncs it, then hard-links it into place. The link fails if the name exists, so an existing blob is never
  overwritten and concurrent writers of one ref converge on a single file. Blobs are ciphertext: the store never
  sees plaintext or keys. There is no delete: erasure is key destruction (D-0004); orphan collection is a later
  functionality.
"""
import os
import secrets
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
