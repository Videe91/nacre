"""Gate test helpers (uniquely named module): capture an episode quickly."""
import uuid

from nacre.capture.record_correction import record_correction
from nacre.capture.record_decision import record_decision
from nacre.capture.record_outcome import Section, record_outcome
from nacre.capture.record_prediction import record_prediction
from nacre.core.event import ActorKind, Mode, Source
from nacre.ledger.append_event import Authorship

AGENT = uuid.UUID(int=5)
AG = dict(actor_kind=ActorKind.AGENT, actor_id=AGENT, source=Source.CHAT, authorship=Authorship.SCOPE_PRINCIPAL)
REVIEW = dict(actor_kind=ActorKind.SYSTEM, actor_id=AGENT, source=Source.REVIEW, authorship=Authorship.INTEGRATION_RESULT)
TOOL = dict(actor_kind=ActorKind.TOOL, actor_id=AGENT, source=Source.TOOL, authorship=Authorship.EXTERNAL)


def k():
    return str(uuid.uuid4())


def episode(s, provider, stream, *, success, sections, who=REVIEW, expected_success="none", decision_stakes=(),
            outcome_stakes=(), mode=None):
    d = record_decision(s, provider, stream_id=stream, idempotency_key=k(), decision_text="d", stakes=decision_stakes,
                        mode=mode, **AG).envelope
    pred = None
    if expected_success != "none":
        pred = record_prediction(s, provider, stream_id=stream, idempotency_key=k(), decision_id=d.event_id,
                                 expected_outcome="x", expected_success=expected_success, **AG).envelope.event_id
    return record_outcome(s, provider, stream_id=stream, idempotency_key=k(), outcome_for=d.event_id, success=success,
                          sections=tuple(Section(r, t) for r, t in sections), evaluates_prediction=pred,
                          stakes=outcome_stakes, mode=mode, **who).envelope


def person_correction(s, provider, stream, target):
    return record_correction(s, provider, stream_id=stream, idempotency_key=k(), correction_of=target, text="no",
                             actor_kind=ActorKind.PERSON, actor_id=AGENT, source=Source.CHAT,
                             authorship=Authorship.SCOPE_PRINCIPAL).envelope


__all__ = ["episode", "person_correction", "k", "Mode", "TOOL", "REVIEW"]
