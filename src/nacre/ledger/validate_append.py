"""
Functionality: Validate one append request and derive its trust, before anything touches the database.
Owns: the AppendRequest shape, request validation (types, ids, short identifiers, time basis and precision,
  idempotency-key form, person and attachment metadata), and trust derivation from source + authorship.
Public entry: validate_append(), AppendRequest, Authorship, AppendError
Decisions: D-0002, D-0012, D-0013
Assumptions: A-0009, A-0012
Notes: Split out of ledger/append_event.py (owner, 2026-09-30) so the write path stays one readable
  orchestration. validate_append() raises AppendError or returns the derived Trust. It enforces:
  - trust by source + authorship per the D-0012 table, default untrusted;
  - an 'observed' occurred_at needs a trusted source (D-0002);
  - occurred_at finer than its stated precision is rejected, never truncated (D1);
  - idempotency keys are canonical UUID v4/v7 (caller-random, D-0012);
  - attachments come with a media type; metadata without an attachment is refused.
"""
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from nacre.core.event import (ActorKind, EventType, Mode, PayloadType, Source, TimeBasis, TimePrecision, Trust)

_SHORT_ID = re.compile(r"^[A-Za-z0-9._:/+-]{1,128}$")
_PERSON_KEYS = {"name", "handle", "email"}
_MEDIA_TYPE = re.compile(r"^[a-z]+/[a-z0-9.+-]{1,100}$")


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
    attachment: bytes | None = None
    attachment_media_type: str | None = None
    attachment_description: str | None = None


class AppendError(ValueError):
    """The request is invalid and nothing was written."""


def validate_append(r: "AppendRequest") -> Trust:
    """Validate the request; return the trust intake assigns it (D-0012). Raises AppendError."""
    _validate(r)
    trust = _trust(r)
    if r.occurred_at_basis == TimeBasis.OBSERVED and trust != Trust.TRUSTED:
        raise AppendError("occurred_at basis 'observed' needs a trusted source (D-0002)")
    return trust


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
    if r.attachment is not None:
        if type(r.attachment) is not bytes:
            raise AppendError("attachment must be bytes")
        if not (type(r.attachment_media_type) is str and _MEDIA_TYPE.match(r.attachment_media_type)):
            raise AppendError("an attachment needs a media type like 'text/plain' or 'image/png'")
        if r.attachment_description is not None and type(r.attachment_description) is not str:
            raise AppendError("attachment_description must be text")
    elif r.attachment_media_type is not None or r.attachment_description is not None:
        raise AppendError("attachment metadata without an attachment")
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
