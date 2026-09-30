"""
Functionality: Append one event to its stream's ledger, end to end.
Owns: (via ledger/validate_append.py: request validation and trust) secret stripping of the body,
  idempotent retries (principal-bound request MAC), subject and key-month choice, sequencing under the
  stream lock, encryption, sealing and the insert.
Public entry: append_event(), AppendResult (AppendRequest, Authorship, AppendError re-exported from validate_append)
Decisions: D-0002, D-0003, D-0004, D-0005, D-0007, D-0008, D-0012, D-0013
Assumptions: A-0007, A-0009, A-0010, A-0014, A-0021
Notes: Runs inside a scoped session (scopes/open_scoped_session.py); RLS admits only the principal's
  streams, and the transaction commits or rolls back with the session.
  Order (D-0003 for the locked part):
    validate → trust → subject/month/key → strip  [unlocked: needs no sequence number; D1, A-0007]
    → take the stream lock → idempotency check → read the head → assign commit_seq, committed_at
    → encrypt → MAC → seal → INSERT.
  recorded_at is set at intake, before the lock.
  Idempotency (D-0012 part B):
  - The key must be a caller-random UUID v4/v7.
  - The MAC covers the caller's PRE-strip request plus the writing principal's id, under the ORIGINAL
    event's data key.
  - Equal MAC → the original event, no new row. Different → IdempotencyConflict. Original key shredded
    → OriginalErased.
  D1 choices:
  - occurred_at finer than its stated precision is REJECTED, never silently truncated (it is asserted data).
  - Structured content is stripped string by string; map keys are left as they are.
  - person is not stripped (it is identity, encrypted anyway); source_ref is stripped.
  - Attachments (D-0013): stored BEFORE the lock, and so before the event commits. Text vs binary is decided by
    CONTENT (valid UTF-8, >= 95% printable), never by the declared media type (owner), so relabelling cannot
    bypass stripping. Text is secret-stripped and marked scan="text-scanned"; binary is stored as given and
    marked scan="unscanned" (A-0021; Phase 3 gate: extract and scan). Metadata goes in the encrypted body.
"""
import hmac
from dataclasses import dataclass, field, fields
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from uuid import UUID

import psycopg

from nacre.core.encode_cbor import encode_cbor
from nacre.core.event import (ENVELOPE_VERSION, ActorKind, Envelope, EventType, Mode, PayloadType, Source,
                              TimeBasis, TimePrecision, Trust, TrustBasis, new_event_id)
from nacre.core.blob_store import BlobStore
from nacre.core.root_key_provider import RootKeyProvider
from nacre.keys.encrypt_payload import MacPurpose, derive_mac, encrypt_payload
from nacre.keys.get_or_create_key import get_or_create_key, load_key
from nacre.ledger.seal_event import GENESIS_PREV_HASH, seal_event
from nacre.ledger.store_attachment import store_attachment
from nacre.ledger.validate_append import AppendError, AppendRequest, Authorship, validate_append  # noqa: F401 (re-exported)
from nacre.ledger.strip_secrets import strip_secrets
from nacre.scopes.open_scoped_session import ScopedSession

_PRINTABLE_SHARE = 0.95   # D1: "mostly printable" = at least 95% of characters printable or whitespace


@dataclass(frozen=True)
class AppendResult:
    envelope: Envelope
    created: bool                                   # False: an idempotent retry returned the original
    redactions: tuple[str, ...] = field(default=())
    public_credentials: tuple[str, ...] = field(default=())


class IdempotencyConflict(AppendError):
    """The idempotency key was already used, in this stream, for a different request or principal."""


class OriginalErased(IdempotencyConflict):
    """The idempotency key names an event whose key has been shredded, so the retry cannot be verified."""


def append_event(session: ScopedSession, provider: RootKeyProvider, request: AppendRequest,
                 blob_store: BlobStore | None = None) -> AppendResult:
    """Validate, strip, encrypt, seal and insert one event; or return the original on an exact retry."""
    recorded_at = datetime.now(UTC)
    trust = validate_append(request)
    conn, stream = session.conn, request.stream_id
    try:
        mac_input = encode_cbor(_mac_input(request, session.access.principal_id))
    except ValueError as exc:
        raise AppendError(f"request content is outside the body subset (D-0008): {exc}") from None

    # Work that needs no sequence number happens BEFORE the stream lock (D1), so the serialised section
    # is only: idempotency check, head, commit_seq/committed_at, encrypt, MAC, seal, insert (D-0003 order).
    subject = request.subject_id or (request.actor_id if request.actor_kind == ActorKind.PERSON
                                     and request.event_type in (EventType.STATEMENT, EventType.MESSAGE) else stream)
    body, redactions, public = _body(request)
    key = get_or_create_key(conn, provider, stream, subject, date(recorded_at.year, recorded_at.month, 1))
    attachment_ref = attachment_sha256 = None
    if request.attachment is not None:
        if blob_store is None:
            raise AppendError("an attachment needs a blob store")
        data, attachment_redactions, scanned = _strip_attachment(request)
        body["attachment"]["scan"] = "text-scanned" if scanned else "unscanned"
        redactions = redactions + attachment_redactions
        if attachment_redactions:
            body["redactions"] = list(redactions)
        attachment_ref, attachment_sha256 = store_attachment(conn, key, blob_store, data)   # before commit (D-0013)

    conn.execute("SELECT pg_advisory_xact_lock(ledger.stream_lock_key(%s))", (stream,))
    existing = _row(conn, "stream_id = %s AND idempotency_key = %s", (stream, request.idempotency_key))
    if existing is not None:
        original_key = load_key(conn, provider, existing.key_id)
        if original_key is None:
            raise OriginalErased(f"idempotency key {request.idempotency_key} names an erased event")
        if not hmac.compare_digest(derive_mac(original_key, MacPurpose.REQUEST_MAC, mac_input), existing.request_mac):
            raise IdempotencyConflict(f"idempotency key {request.idempotency_key} was used for a different request")
        return AppendResult(existing, created=False)

    if request.caused_by is not None and _row(conn, "event_id = %s AND stream_id = %s", (request.caused_by, stream)) is None:
        raise AppendError("caused_by must be an earlier event of the same stream (D-0002)")
    head = conn.execute("SELECT commit_seq, hash FROM ledger.events WHERE stream_id = %s "
                        "ORDER BY commit_seq DESC LIMIT 1", (stream,)).fetchone()
    seq, prev_hash = (head[0] + 1, bytes(head[1])) if head else (1, GENESIS_PREV_HASH)
    values = {name: getattr(request, name, None) for name in (f.name for f in fields(Envelope))}
    values.update(envelope_version=ENVELOPE_VERSION, event_id=new_event_id(), commit_seq=seq, recorded_at=recorded_at,
                  committed_at=datetime.now(UTC), trust=trust, trust_basis=TrustBasis.ASSERTED, key_id=key.key_id,
                  attachment_ref=attachment_ref, attachment_sha256=attachment_sha256, prev_hash=prev_hash)
    aad = {k: values[k] for k in ("envelope_version", "event_id", "stream_id", "key_id", "event_type", "payload_type")}
    values["body_ciphertext"] = encrypt_payload(conn, key, aad, body)
    values["request_mac"] = derive_mac(key, MacPurpose.REQUEST_MAC, mac_input)
    values.pop("hash")
    values["hash"] = seal_event(values)
    columns = [f.name for f in fields(Envelope)]
    conn.execute(f"INSERT INTO ledger.events ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))})",
                 [values[c] for c in columns])
    return AppendResult(Envelope(**values), created=True, redactions=redactions, public_credentials=public)


# ---- MAC input, body, rows ---------------------------------------------------------------------------
def _plain(value):
    if isinstance(value, StrEnum):
        return value.value
    if type(value) is UUID:
        return str(value)
    if type(value) is datetime:
        return (value - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(microseconds=1)   # exact integer µs
    return value


def _mac_input(r: AppendRequest, principal_id: UUID) -> dict:
    """Every caller-supplied field, pre-strip, plus the writing principal (D-0012 part B)."""
    return {"v": 1, "principal": str(principal_id), **{f.name: _plain(getattr(r, f.name)) for f in fields(r)}}


def _body(r: AppendRequest):
    redactions, public = [], set()

    def strip(value):
        if type(value) is str:
            result = strip_secrets(value)
            redactions.extend(result.redactions)
            public.update(result.public_credentials)
            return result.text
        if type(value) is list:
            return [strip(v) for v in value]
        if type(value) is dict:
            return {k: strip(v) for k, v in value.items()}
        return value

    body = {"content_version": r.content_version, "content": strip(r.content)}
    if r.person is not None:
        body["person"] = dict(r.person)
    if r.source_ref is not None:
        body["source_ref"] = strip(r.source_ref)
    if r.attachment is not None:
        body["attachment"] = {"media_type": r.attachment_media_type}
        if r.attachment_description is not None:
            body["attachment"]["description"] = strip(r.attachment_description)
    if redactions:
        body["redactions"] = list(redactions)
    if public:
        body["public_credentials"] = sorted(public)
    return body, tuple(redactions), tuple(sorted(public))


def _strip_attachment(r: AppendRequest) -> tuple[bytes, tuple[str, ...], bool]:
    """(stored bytes, redactions, scanned). Text = valid, mostly printable UTF-8, whatever the declared type."""
    try:
        text = r.attachment.decode("utf-8")
    except UnicodeDecodeError:
        return r.attachment, (), False
    printable = sum(c.isprintable() or c in "\n\r\t" for c in text)
    if text and printable / len(text) < _PRINTABLE_SHARE:
        return r.attachment, (), False
    result = strip_secrets(text)
    return result.text.encode("utf-8"), result.redactions, True


_ENUMS = {"event_type": EventType, "payload_type": PayloadType, "actor_kind": ActorKind, "source": Source,
          "trust": Trust, "occurred_at_basis": TimeBasis, "occurred_at_precision": TimePrecision, "mode": Mode,
          "trust_basis": TrustBasis}


def _row(conn: psycopg.Connection, where: str, params) -> Envelope | None:
    columns = [f.name for f in fields(Envelope)]
    row = conn.execute(f"SELECT {', '.join(columns)} FROM ledger.events WHERE {where}", params).fetchone()
    if row is None:
        return None
    values = {}
    for name, value in zip(columns, row):
        if isinstance(value, memoryview):
            value = bytes(value)
        if name in _ENUMS and value is not None:
            value = _ENUMS[name](value)
        values[name] = value
    return Envelope(**values)
