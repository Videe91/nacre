"""
Functionality: The ledger event envelope as a data type: its enums, its row shape, and event ids.
Owns: the persisted enum values, the Envelope field set, ENVELOPE_VERSION, new_event_id().
Public entry: Envelope, new_event_id()
Decisions: D-0002, D-0006
Assumptions: A-0009
Notes: Types only, no logic. Validation (UUID-only id fields, charset limits, occurred_at
  basis/precision pairing, truncation) belongs to ledger/append_event.py. Canonical byte order
  belongs to ledger/encode_envelope.py, not to this field order. Enum string values are a
  persistence format: changing one needs a new envelope_version and an ADR.
"""
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

ENVELOPE_VERSION = 1


class EventType(StrEnum):
    MESSAGE = "message"
    ACTION = "action"
    RESULT = "result"
    PREDICTION = "prediction"
    DECISION = "decision"
    OUTCOME = "outcome"
    STATEMENT = "statement"
    MEMORY_EVENT = "memory_event"
    CONFIG_EVENT = "config_event"
    CORRECTION = "correction"
    DELETION_MARKER = "deletion_marker"


class PayloadType(StrEnum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    DIFF = "diff"
    TABLE = "table"
    STRUCTURED = "structured"
    TRACE = "trace"


class ActorKind(StrEnum):
    PERSON = "person"
    AGENT = "agent"
    MODEL = "model"
    TOOL = "tool"
    SYSTEM = "system"


class Source(StrEnum):
    CHAT = "chat"
    GIT = "git"
    CI = "ci"
    REVIEW = "review"
    WEB = "web"
    TOOL = "tool"
    SYSTEM = "system"


class Trust(StrEnum):
    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"


class TimeBasis(StrEnum):
    """MNEXA ADR-0010 rule 2. There is deliberately no 'inferred' member (rule 3)."""
    OBSERVED = "observed"
    ASSERTED = "asserted"


class TimePrecision(StrEnum):
    YEAR = "year"
    MONTH = "month"
    DAY = "day"
    HOUR = "hour"
    MINUTE = "minute"
    SECOND = "second"
    MILLISECOND = "millisecond"
    MICROSECOND = "microsecond"


class Mode(StrEnum):
    NORMAL = "normal"
    INCIDENT = "incident"
    ONBOARDING = "onboarding"
    EXPLORATION = "exploration"


@dataclass(frozen=True, slots=True, kw_only=True)
class Envelope:
    """One committed ledger row, exactly the D-0002 field table. Only commit_seq orders."""
    envelope_version: int
    event_id: UUID
    stream_id: UUID
    org_id: UUID | None
    project_id: UUID | None
    user_id: UUID | None
    agent_id: UUID | None
    task_id: UUID | None
    commit_seq: int
    occurred_at: datetime | None
    occurred_at_basis: TimeBasis | None
    occurred_at_precision: TimePrecision | None
    recorded_at: datetime
    committed_at: datetime
    event_type: EventType
    payload_type: PayloadType
    actor_kind: ActorKind
    actor_id: UUID
    actor_model: str | None
    actor_model_version: str | None
    actor_tool: str | None
    source: Source
    trust: Trust
    caused_by: UUID | None
    cycle_id: UUID | None
    config_version: str | None
    mode: Mode | None
    key_id: UUID
    idempotency_key: str
    request_mac: bytes
    attachment_ref: bytes | None
    attachment_sha256: bytes | None
    body_ciphertext: bytes
    prev_hash: bytes
    hash: bytes


def new_event_id() -> UUID:
    """A time-sortable UUIDv7 (RFC 9562), from the Python 3.14 stdlib (D-0006)."""
    return uuid.uuid7()
