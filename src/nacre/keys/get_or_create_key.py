"""
Functionality: Resolve data keys: get or create the key for (stream, subject, month) to write, or
  load a key by id to read, unwrapping through the stream master key and the root key.
Owns: stream master key creation, data key creation, the data-key wrap format, and the race-safe
  get-or-create under concurrency.
Public entry: get_or_create_key(), load_key(), DataKey
Decisions: D-0004, D-0005
Assumptions: A-0008
Notes: Must run inside a scoped session (scopes/open_scoped_session.py). RLS then admits only the
  principal's streams: creating needs append on the stream, loading needs read.
  Key hierarchy (D-0004): root key (RootKeyProvider) wraps the stream master key, which is bound to
  the stream id; the master key wraps data keys. Data-key wrap:
    nonce(12) | AES-256-GCM(master, dek), AAD = "nacre-dek-wrap-v1" | key_id | stream_id | subject_id | month
  so a wrapped data key copied onto another row fails to unwrap.
  System subject = the stream id itself (D-0004, schema 0003).
  Get-or-create: INSERT ... ON CONFLICT DO NOTHING, then SELECT. Under READ COMMITTED a concurrent
  creator's row is visible after the conflict resolves, so both callers get the same key.
  load_key returns None when the data key or its stream master key no longer exists: that is a
  shredded key, since the caller can only ask about streams it can read.
  No cache in Phase 1. Any cache added later must be invalidated on rotation (D-0004 amendment 7). (D1)
"""
import secrets
from dataclasses import dataclass, field
from datetime import date
from uuid import UUID

import psycopg
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from nacre.core.event import new_event_id
from nacre.core.root_key_provider import RootKeyProvider, WrappedKey

KEY_BYTES = 32
_NONCE_BYTES = 12
_DEK_AAD_PREFIX = b"nacre-dek-wrap-v1"


@dataclass(frozen=True, slots=True)
class DataKey:
    key_id: UUID
    stream_id: UUID
    subject_id: UUID
    month: date
    material: bytes = field(repr=False)


class KeyResolutionError(LookupError):
    """A key could not be resolved (not visible, tampered, or its root version is gone)."""


def get_or_create_key(conn: psycopg.Connection, provider: RootKeyProvider,
                      stream_id: UUID, subject_id: UUID, month: date) -> DataKey:
    """The data key for (stream, subject, month), creating the master and data keys if needed."""
    if type(month) is not date or month.day != 1:
        raise ValueError("month must be a date on the first day of a month")
    master = _master_key(conn, provider, stream_id, create=True)
    conn.execute("""INSERT INTO keys.data_keys (key_id, stream_id, subject_id, month, wrapped_key)
                    VALUES (%s, %s, %s, %s, %s) ON CONFLICT (stream_id, subject_id, month) DO NOTHING""",
                 _new_dek_row(master, stream_id, subject_id, month))
    key_id, wrapped = conn.execute("""SELECT key_id, wrapped_key FROM keys.data_keys
                                       WHERE stream_id = %s AND subject_id = %s AND month = %s""",
                                   (stream_id, subject_id, month)).fetchone()
    return DataKey(key_id, stream_id, subject_id, month,
                   _unwrap_dek(master, key_id, stream_id, subject_id, month, bytes(wrapped)))


def load_key(conn: psycopg.Connection, provider: RootKeyProvider, key_id: UUID) -> DataKey | None:
    """The data key with this id, or None if it (or its stream master key) has been shredded."""
    row = conn.execute("SELECT stream_id, subject_id, month, wrapped_key FROM keys.data_keys WHERE key_id = %s",
                       (key_id,)).fetchone()
    if row is None:
        return None
    stream_id, subject_id, month, wrapped = row
    master = _master_key(conn, provider, stream_id, create=False)
    if master is None:
        return None
    return DataKey(key_id, stream_id, subject_id, month,
                   _unwrap_dek(master, key_id, stream_id, subject_id, month, bytes(wrapped)))


def _master_key(conn, provider, stream_id, create):
    select = "SELECT root_key_version, wrapped_key FROM keys.stream_master_keys WHERE stream_id = %s"
    row = conn.execute(select, (stream_id,)).fetchone()
    if row is None and create:
        wrapped = provider.wrap(secrets.token_bytes(KEY_BYTES), stream_id.bytes)
        conn.execute("""INSERT INTO keys.stream_master_keys (stream_id, root_key_version, wrapped_key)
                        VALUES (%s, %s, %s) ON CONFLICT (stream_id) DO NOTHING""",
                     (stream_id, wrapped.root_key_version, wrapped.wrapped))
        row = conn.execute(select, (stream_id,)).fetchone()
    if row is None:
        return None
    try:
        return provider.unwrap(WrappedKey(row[0], bytes(row[1])), stream_id.bytes)
    except (KeyError, ValueError) as exc:
        raise KeyResolutionError(f"stream master key of {stream_id} cannot be unwrapped: {exc}") from None


def _dek_aad(key_id, stream_id, subject_id, month):
    return _DEK_AAD_PREFIX + key_id.bytes + stream_id.bytes + subject_id.bytes + month.isoformat().encode()


def _new_dek_row(master, stream_id, subject_id, month):
    key_id, nonce = new_event_id(), secrets.token_bytes(_NONCE_BYTES)
    sealed = AESGCM(master).encrypt(nonce, secrets.token_bytes(KEY_BYTES), _dek_aad(key_id, stream_id, subject_id, month))
    return key_id, stream_id, subject_id, month, nonce + sealed


def _unwrap_dek(master, key_id, stream_id, subject_id, month, wrapped):
    try:
        return AESGCM(master).decrypt(wrapped[:_NONCE_BYTES], wrapped[_NONCE_BYTES:],
                                      _dek_aad(key_id, stream_id, subject_id, month))
    except (InvalidTag, ValueError):
        raise KeyResolutionError(f"data key {key_id} failed authentication (tampered or moved)") from None
