"""Recall test helpers (uniquely named module): grounded beliefs written through the real capture -> propose ->
promote path, so every version gets its index entry exactly as in production."""
import uuid

from nacre.capture.record_decision import record_decision
from nacre.capture.record_outcome import Section, record_outcome
from nacre.core.event import ActorKind, Source
from nacre.ledger.append_event import Authorship
from nacre.stores.promote_if_supported import promote_if_supported
from nacre.stores.propose_lesson import propose_lesson

AGENT, REVIEWER = uuid.UUID(int=21), uuid.UUID(int=22)


def belief(s, kp, stream, correction: str, nucleus: str):
    """A single-source belief from one trusted review correction; returns the Promotion."""
    d = record_decision(s, kp, stream_id=stream, actor_kind=ActorKind.AGENT, actor_id=AGENT, source=Source.CHAT,
                        authorship=Authorship.SCOPE_PRINCIPAL, idempotency_key=str(uuid.uuid4()),
                        decision_text="first attempt").envelope
    o = record_outcome(s, kp, stream_id=stream, actor_id=REVIEWER, idempotency_key=str(uuid.uuid4()),
                       outcome_for=d.event_id, success=False, actor_kind=ActorKind.SYSTEM, source=Source.REVIEW,
                       authorship=Authorship.INTEGRATION_RESULT,
                       sections=(Section("status", "FAIL"), Section("correction", correction))).envelope
    p = propose_lesson(s, kp, stream_id=stream, decision_id=d.event_id, outcome_id=o.event_id, section_index=1,
                       span=(0, len(correction)), nucleus=nucleus).event_id
    return promote_if_supported(s, kp, stream, p)
