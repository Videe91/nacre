"""
Functionality: Append one event to its stream's ledger, end to end.
Owns: request validation, trust derivation from source + authorship, secret stripping of the body,
  idempotent retries (principal-bound request MAC), subject and key-month choice, sequencing under the
  stream lock, encryption, sealing and the insert.
Public entry: append_event(), AppendRequest, AppendResult, Authorship
Decisions: D-0002, D-0003, D-0004, D-0005, D-0007, D-0008, D-0012
Assumptions: A-0007, A-0009, A-0010, A-0014
Notes: Runs inside a scoped session (scopes/open_scoped_session.py); RLS admits only the principal's
  streams, and the transaction commits or rolls back with the session.
  Order (D-0003):
    validate → trust → take the stream lock → idempotency check → subject/month/key → strip →
    read the head → assign commit_seq, committed_at → encrypt → MAC → seal → INSERT.
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
"""
import hmac
import re
import uuid
from dataclasses import dataclass, field, fields
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from uuid import UUID

import psycopg

from nacre.core.encode_cbor import encode_cbor
from nacre.core.event import (ENVELOPE_VERSION, ActorKind, Envelope, EventType, Mode, PayloadType, Source,
                              TimeBasis, TimePrecision, Trust, TrustBasis, new_event_id)
from nacre.core.root_key_provider import RootKeyProvider
from nacre.keys.encrypt_payload import MacPurpose, derive_mac, encrypt_payload
from nacre.keys.get_or_create_key import get_or_create_key, load_key
from nacre.ledger.seal_event import GENESIS_PREV_HASH, seal_event
from nacre.ledger.strip_secrets import strip_secrets
from nacre.scopes.open_scoped_session import ScopedSession

_SHORT_ID = re.compile(r"^[A-Za-z0-9._:/+-]{1,128}$")
_PERSON_KEYS = {"name", "handle", "email"}


class Authorship(StrEnum):
    """D-0012 part A: who authored the content, as the caller asserts it (trust_basis = asserted)."""
    SCOPE_PRINCIPAL = "scope_principal"
    INTEGRATION_RESULT = "integration_result"
    EXTERNAL = "external"


@dataclass(frozen=True, kw_only=True)
class AppendRequest:
    stream_id: UUID
    event_type: EventType
    payload_type: PayloadType
    actor_kind: ActorKind
    actor_id: UUID
    source: Source
    idempotency_key: str
    content: object
    content_version: int = 1
    authorship: Authorship = Authorship.EXTERNAL
    org_id: UUID | None = None
    project_id: UUID | None = None
    user_id: UUID | None = None
    agent_id: UUID | None = None
    task_id: UUID | None = None
    occurred_at: datetime | None = None
    occurred_at_basis: TimeBasis | None = None
    occurred_at_precision: TimePrecision | None = None
    actor_model: str | None = None
    actor_model_version: str | None = None
    actor_tool: str | None = None
    caused_by: UUID | None = None
    cycle_id: UUID | None = None
    config_version: str | None = None
    mode: Mode | None = None
    person: dict | None = None
    source_ref: str | None = None
    subject_id: UUID | None = None


@dataclass(frozen=True)
class AppendResult:
    envelope: Envelope
    created: bool                                   # False: an idempotent retry returned the original
    redactions: tuple[str, ...] = field(default=())
    public_credentials: tuple[str, ...] = field(default=())


class AppendError(ValueError):
    """The request is invalid and nothing was written."""


class IdempotencyConflict(AppendError):
    """The idempotency key was already used, in this stream, for a different request or principal."""


class OriginalErased(IdempotencyConflict):
    """The idempotency key names an event whose key has been shredded, so the retry cannot be verified."""


def append_event(session: ScopedSession, provider: RootKeyProvider, request: AppendRequest) -> AppendResult:
    """Validate, strip, encrypt, seal and insert one event; or return the original on an exact retry."""
    recorded_at = datetime.now(UTC)
    _validate(request)
    trust = _trust(request)
    if request.occurred_at_basis == TimeBasis.OBSERVED and trust != Trust.TRUSTED:
        raise AppendError("occurred_at basis 'observed' needs a trusted source (D-0002)")
    conn, stream = session.conn, request.stream_id
    try:
        mac_input = encode_cbor(_mac_input(request, session.access.principal_id))
    except ValueError as exc:
        raise AppendError(f"request content is outside the body subset (D-0008): {exc}") from None

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
    subject = request.subject_id or (request.actor_id if request.actor_kind == ActorKind.PERSON
                                     and request.event_type in (EventType.STATEMENT, EventType.MESSAGE) else stream)
    key = get_or_create_key(conn, provider, stream, subject, date(recorded_at.year, recorded_at.month, 1))
    body, redactions, public = _body(request)

    head = conn.execute("SELECT commit_seq, hash FROM ledger.events WHERE stream_id = %s "
                        "ORDER BY commit_seq DESC LIMIT 1", (stream,)).fetchone()
    seq, prev_hash = (head[0] + 1, bytes(head[1])) if head else (1, GENESIS_PREV_HASH)
    values = {name: getattr(request, name, None) for name in (f.name for f in fields(Envelope))}
    values.update(envelope_version=ENVELOPE_VERSION, event_id=new_event_id(), commit_seq=seq, recorded_at=recorded_at,
                  committed_at=datetime.now(UTC), trust=trust, trust_basis=TrustBasis.ASSERTED, key_id=key.key_id,
                  attachment_ref=None, attachment_sha256=None, prev_hash=prev_hash)
    aad = {k: values[k] for k in ("envelope_version", "event_id", "stream_id", "key_id", "event_type", "payload_type")}
    values["body_ciphertext"] = encrypt_payload(conn, key, aad, body)
    values["request_mac"] = derive_mac(key, MacPurpose.REQUEST_MAC, mac_input)
    values.pop("hash")
    values["hash"] = seal_event(values)
    columns = [f.name for f in fields(Envelope)]
    conn.execute(f"INSERT INTO ledger.events ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))})",
                 [values[c] for c in columns])
    return AppendResult(Envelope(**values), created=True, redactions=redactions, public_credentials=public)


# ---- validation and trust ---------------------------------------------------------------------------
def _validate(r: AppendRequest) -> None:
    for name, kind in (("event_type", EventType), ("payload_type", PayloadType), ("actor_kind", ActorKind),
                       ("source", Source), ("authorship", Authorship)):
        if not isinstance(getattr(r, name), kind):
            raise AppendError(f"{name} must be a {kind.__name__}")
    for name in ("stream_id", "actor_id", "org_id", "project_id", "user_id", "agent_id", "task_id",
                 "caused_by", "cycle_id", "subject_id"):
        value = getattr(r, name)
        if value is not None and type(value) is not UUID:
            raise AppendError(f"{name} must be a UUID (never a natural identifier, D-0002)")
    for name in ("actor_model", "actor_model_version", "actor_tool", "config_version"):
        value = getattr(r, name)
        if value is not None and not (type(value) is str and _SHORT_ID.match(value)):
            raise AppendError(f"{name} must match {_SHORT_ID.pattern}")
    if r.mode is not None and not isinstance(r.mode, Mode):
        raise AppendError("mode must be a Mode")
    try:
        key = uuid.UUID(r.idempotency_key)
    except (ValueError, AttributeError, TypeError):
        key = None
    if key is None or key.version not in (4, 7) or str(key) != r.idempotency_key:
        raise AppendError("idempotency_key must be a caller-random UUID v4/v7 in canonical form (D-0012)")
    if type(r.content_version) is not int or r.content_version < 1:
        raise AppendError("content_version must be an int >= 1")
    if r.event_type == EventType.CORRECTION and r.caused_by is None:
        raise AppendError("a correction must set caused_by (D-0002)")
    if r.person is not None and (type(r.person) is not dict or set(r.person) - _PERSON_KEYS
                                 or any(type(v) is not str for v in r.person.values())):
        raise AppendError(f"person must map text keys from {sorted(_PERSON_KEYS)} to text")
    if r.source_ref is not None and type(r.source_ref) is not str:
        raise AppendError("source_ref must be text")
    _validate_time(r)


_TRUNCATE = {TimePrecision.YEAR: dict(month=1, day=1, hour=0, minute=0, second=0, microsecond=0),
             TimePrecision.MONTH: dict(day=1, hour=0, minute=0, second=0, microsecond=0),
             TimePrecision.DAY: dict(hour=0, minute=0, second=0, microsecond=0),
             TimePrecision.HOUR: dict(minute=0, second=0, microsecond=0),
             TimePrecision.MINUTE: dict(second=0, microsecond=0),
             TimePrecision.SECOND: dict(microsecond=0),
             TimePrecision.MILLISECOND: {}, TimePrecision.MICROSECOND: {}}


def _validate_time(r: AppendRequest) -> None:
    parts = (r.occurred_at, r.occurred_at_basis, r.occurred_at_precision)
    if all(p is None for p in parts):
        return
    if any(p is None for p in parts):
        raise AppendError("occurred_at, its basis and its precision go together (MNEXA ADR-0010 rules 2, 4)")
    if type(r.occurred_at) is not datetime or r.occurred_at.utcoffset() is None:
        raise AppendError("occurred_at must be a timezone-aware datetime")
    if not isinstance(r.occurred_at_basis, TimeBasis) or not isinstance(r.occurred_at_precision, TimePrecision):
        raise AppendError("occurred_at basis/precision must be TimeBasis/TimePrecision")
    utc = r.occurred_at.astimezone(UTC)
    finer = utc != utc.replace(**_TRUNCATE[r.occurred_at_precision])
    if r.occurred_at_precision == TimePrecision.MILLISECOND:
        finer = utc.microsecond % 1000 != 0
    if finer:
        raise AppendError("occurred_at is finer than its stated precision (MNEXA ADR-0010 rule 4)")


def _trust(r: AppendRequest) -> Trust:
    """D-0012 part A, as corrected by the owner. Default untrusted."""
    if r.source in (Source.WEB, Source.TOOL) or r.authorship == Authorship.EXTERNAL:
        return Trust.UNTRUSTED
    if r.authorship == Authorship.INTEGRATION_RESULT:
        if r.source in (Source.GIT, Source.CI, Source.REVIEW, Source.SYSTEM) and r.payload_type == PayloadType.STRUCTURED:
            return Trust.TRUSTED
        raise AppendError("integration_result authorship needs a git/ci/review/system source and structured payload (D-0012)")
    return Trust.TRUSTED if r.source in (Source.CHAT, Source.GIT, Source.REVIEW, Source.SYSTEM) else Trust.UNTRUSTED


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
    if redactions:
        body["redactions"] = list(redactions)
    if public:
        body["public_credentials"] = sorted(public)
    return body, tuple(redactions), tuple(sorted(public))


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
