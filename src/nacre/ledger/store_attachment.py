"""
Functionality: Encrypt and store one attachment under the event's data key, deduplicated within that key.
Owns: the attachment ref (keyed fingerprint), the 16 MiB limit, the attachment AAD, dedup, and the stored blob's
  sha256.
Public entry: store_attachment(), MAX_ATTACHMENT_BYTES
Decisions: D-0002, D-0004, D-0008, D-0013, D-0015
Assumptions: A-0015, A-0022
Notes: D-0013:
  - ref = HMAC-SHA256 of the plaintext under the data key's attachment_ref sub-key. It is also the blob store key.
  - The blob uses the D-0008 ciphertext layout with AAD = "nacre-attachment-v1" | ref, bound to the ref and not
    to an event, so one blob can serve every event under the same key that attaches the same bytes.
  - If the ref already exists, the stored blob is reused, and ITS sha256 goes into the event.
  - Called on the write path BEFORE the event commits (owner addition): the file exists before any event can
    point to it. A rolled-back append leaves a harmless orphan.
  - D-0015 amendment 1: takes the SHARED per-ref advisory lock (transaction-level, so held until the append commits
    or rolls back) BEFORE the existence check and the write. The collector needs the exclusive lock to delete, so it
    can never remove a blob an in-flight append has checked or reused.
"""
import hashlib

import psycopg

from nacre.core.blob_store import BlobStore
from nacre.keys.encrypt_payload import MacPurpose, derive_mac, seal_bytes
from nacre.keys.get_or_create_key import DataKey

MAX_ATTACHMENT_BYTES = 16 * 1024 * 1024
AAD_PREFIX = b"nacre-attachment-v1"


class AttachmentError(ValueError):
    """The attachment cannot be stored."""


def store_attachment(conn: psycopg.Connection, key: DataKey, store: BlobStore, plaintext: bytes) -> tuple[bytes, bytes]:
    """(attachment_ref, attachment_sha256) for `plaintext`, encrypting and writing the blob if it is new."""
    if type(plaintext) is not bytes:
        raise AttachmentError("attachments are bytes")
    if len(plaintext) > MAX_ATTACHMENT_BYTES:
        raise AttachmentError(f"attachment is {len(plaintext)} bytes; the Phase 1 limit is {MAX_ATTACHMENT_BYTES}")
    ref = derive_mac(key, MacPurpose.ATTACHMENT_REF, plaintext)
    conn.execute("SELECT pg_advisory_xact_lock_shared(ledger.attachment_lock_namespace(), ledger.attachment_lock_key(%s))",
                 (ref,))
    if not store.exists(ref):
        store.put_if_absent(ref, seal_bytes(conn, key, AAD_PREFIX + ref, plaintext))
    return ref, hashlib.sha256(store.get(ref)).digest()
