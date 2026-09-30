"""
Functionality: Read one event's attachment, verifying its fingerprints on every read.
Owns: the missing-blob, tampered-blob and wrong-plaintext checks, and decryption under the event's data key.
Public entry: read_attachment()
Decisions: D-0004, D-0005, D-0013
Assumptions: none
Notes: Owner addition (D-0013): every read verifies (1) sha256(stored blob) == the event's attachment_sha256 and
  (2) HMAC(plaintext) == the event's attachment_ref. A mismatch raises an error; no bytes are ever returned
  unverified. A shredded key returns Shredded, as for bodies. The stream must be readable in the session.
"""
import hashlib
import hmac

from nacre.core.blob_store import BlobStore
from nacre.core.event import Envelope
from nacre.core.root_key_provider import RootKeyProvider
from nacre.keys.decrypt_payload import DecryptError, Shredded, check_header, open_bytes
from nacre.keys.encrypt_payload import MacPurpose, derive_mac
from nacre.keys.get_or_create_key import load_key
from nacre.ledger.store_attachment import AAD_PREFIX
from nacre.scopes.open_scoped_session import ScopedSession


class AttachmentReadError(LookupError):
    """The attachment is missing, tampered with, or not readable here."""


def read_attachment(session: ScopedSession, provider: RootKeyProvider, store: BlobStore, envelope: Envelope) -> bytes | Shredded:
    """The verified plaintext of the event's attachment, or Shredded."""
    if envelope.attachment_ref is None:
        raise AttachmentReadError("the event has no attachment")
    if envelope.stream_id not in session.access.read_streams:
        raise AttachmentReadError("stream is not readable in this session")
    try:
        blob = store.get(envelope.attachment_ref)
    except KeyError:
        raise AttachmentReadError("attachment blob is missing from the store") from None
    if not hmac.compare_digest(hashlib.sha256(blob).digest(), envelope.attachment_sha256):
        raise AttachmentReadError("attachment blob does not match the event's attachment_sha256 (tampered)")
    try:
        if check_header(blob) != envelope.key_id:
            raise AttachmentReadError("attachment blob is not encrypted under the event's key")
        key = load_key(session.conn, provider, envelope.key_id)
        if key is None:
            return Shredded(envelope.key_id)
        plaintext = open_bytes(key.material, AAD_PREFIX + envelope.attachment_ref, blob)
    except DecryptError as exc:
        raise AttachmentReadError(f"attachment failed authentication: {exc}") from None
    if not hmac.compare_digest(derive_mac(key, MacPurpose.ATTACHMENT_REF, plaintext), envelope.attachment_ref):
        raise AttachmentReadError("attachment plaintext does not match the event's attachment_ref")
    return plaintext
