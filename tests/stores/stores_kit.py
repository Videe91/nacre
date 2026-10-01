"""Stores test helpers (uniquely named module): capture episodes and grounded proposals."""
import uuid

from nacre.capture.record_decision import record_decision
from nacre.capture.record_outcome import Section, record_outcome
from nacre.core.event import ActorKind, Source
from nacre.ledger.append_event import Authorship
from nacre.stores.propose_lesson import propose_lesson

AGENT, REVIEWER = uuid.UUID(int=11), uuid.UUID(int=12)
RULE = "Retry VX-41 failures after 137 ms with header X-Relay: cobalt."


def episode(s, kp, stream, correction=RULE, *, trusted=True, role="correction", success=False):
    who = (dict(actor_kind=ActorKind.SYSTEM, source=Source.REVIEW, authorship=Authorship.INTEGRATION_RESULT) if trusted
           else dict(actor_kind=ActorKind.TOOL, source=Source.TOOL, authorship=Authorship.EXTERNAL))
    d = record_decision(s, kp, stream_id=stream, actor_kind=ActorKind.AGENT, actor_id=AGENT, source=Source.CHAT,
                        authorship=Authorship.SCOPE_PRINCIPAL, idempotency_key=str(uuid.uuid4()),
                        decision_text="retry immediately").envelope
    o = record_outcome(s, kp, stream_id=stream, actor_id=REVIEWER, idempotency_key=str(uuid.uuid4()),
                       outcome_for=d.event_id, success=success,
                       sections=(Section("status", "FAIL: VX-41 retries exhausted"), Section(role, correction)), **who).envelope
    return d.event_id, o.event_id


def propose(s, kp, stream, d, o, *, section=1, text=RULE, nucleus="Retry VX-41 failures after 137 ms", support=None):
    start = 0 if support is None else text.index(support)
    end = len(text) if support is None else start + len(support)
    return propose_lesson(s, kp, stream_id=stream, decision_id=d, outcome_id=o, section_index=section,
                          span=(start, end), nucleus=nucleus).event_id


def grounded_belief(s, kp, stream, correction=RULE, **kw):
    from nacre.stores.promote_if_supported import promote_if_supported
    d, o = episode(s, kp, stream, correction, **kw)
    nucleus = "Retry VX-41 failures after 137 ms" if correction == RULE else correction.rstrip(".")
    return promote_if_supported(s, kp, stream, propose(s, kp, stream, d, o, text=correction, nucleus=nucleus))
