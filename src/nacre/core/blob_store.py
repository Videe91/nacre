"""
Functionality: The interface every attachment blob store implements (interface only, no logic).
Owns: the BlobStore protocol and its write-once contract.
Public entry: BlobStore
Decisions: D-0004, D-0006, D-0015
Assumptions: none
Notes: Blobs are already encrypted when they arrive; stores never see plaintext or keys.
  `ref` is the event's attachment_ref: a 32-byte HMAC of the plaintext under a data-key-derived
  key, so identical content under the same data key has the same ref (D-0004 dedup within key).
  Write-once: put_if_absent never overwrites an existing ref, because the ledger is immutable and
  attachment_sha256 in the sealed event must keep matching the stored blob.
  Delete (D-0015 amendment 3): ONLY through ledger/collect_orphan_blobs.py, ONLY for unreferenced blobs, under
  the exclusive per-ref lock. Erasure is never done by deleting bytes: it is key destruction (D-0004).
  Implementations: ledger/local_disk_blob_store.py (Phase 1); S3-compatible later (D-0006).
"""
from collections.abc import Iterator
from typing import Protocol, runtime_checkable

REF_LENGTH = 32


@runtime_checkable
class BlobStore(Protocol):
    def put_if_absent(self, ref: bytes, blob: bytes) -> bool:
        """Store `blob` under `ref` unless `ref` exists. True if written, False if already present.
        Must be atomic: a concurrent reader sees either nothing or the complete blob."""
        ...

    def get(self, ref: bytes) -> bytes:
        """Return the blob stored under `ref`. Raises KeyError if absent."""
        ...

    def exists(self, ref: bytes) -> bool:
        """True if a blob is stored under `ref`."""
        ...

    def list_refs(self) -> Iterator[tuple[bytes, float]]:
        """Every stored ref with its age in seconds (since it was written)."""
        ...

    def delete(self, ref: bytes) -> bool:
        """Remove the blob under `ref`; False if it was absent. Callers: collect_orphan_blobs only (D-0015)."""
        ...

    def remove_stale_temp(self, min_age_seconds: float) -> int:
        """Remove unfinished write debris older than `min_age_seconds`; returns how many were removed."""
        ...
