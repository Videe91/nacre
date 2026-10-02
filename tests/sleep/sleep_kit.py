"""Sleep-pass test helpers (uniquely named module): a scripted fake provider and proposition JSON builders."""
import json
from dataclasses import dataclass, field

from nacre.core.model_provider import ModelResponse, ProviderError, Usage

MODEL = "gpt-4o-mini-2024-07-18"


@dataclass
class Fake:
    script: list = field(default_factory=list)       # str (JSON text) | Exception, one per live call
    name: str = "openai"
    replay: bool = False
    requests: list = field(default_factory=list)

    def complete(self, request, *, timeout_s):
        self.requests.append(request)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return ModelResponse(text=item, finish_reason="completed", usage=Usage(1000, 200, None), response_id="r",
                             model_reported=MODEL, latency_ms=5)


def props(*items):
    """items: (section, quote, nucleus[, qualifiers])"""
    return json.dumps({"propositions": [{"section": i[0], "quote": i[1], "nucleus": i[2],
                                         "qualifiers": [{"type": t, "text": x} for t, x in (i[3] if len(i) > 3 else [])]}
                                        for i in items]})


def crash():
    return ProviderError("connection reset", retryable=False, error_class="APIConnectionError")


# ---- D-0020 amendment 1: episodes whose outcome is recorded against an ACTION ----

def record_family_via_action(session, kp, stream, family, *, link="decision", decision_actor=None):
    """Family 014's first episode as decision -> action -> outcome (same actors as eval/load_mnexa_family.py).

    link: "decision" (a real `execution_of` to the decision), "none" (no reference), or a UUID the action names in its
    `execution_of` reference, written raw (bypassing capture's same-stream/type validation) to model a bad link.
    decision_actor: a person id to key the decision under its own key (so it can be erased alone).
    Returns (decision_id, action_id, outcome_id)."""
    import uuid

    from nacre.capture.record_action import record_action
    from nacre.capture.record_decision import record_decision
    from nacre.capture.record_outcome import record_outcome
    from nacre.core.event import ActorKind, EventType, PayloadType, Source
    from nacre.eval.load_mnexa_family import HARNESS_AGENT, HARNESS_REVIEWER, outcome_sections
    from nacre.ledger.append_event import AppendRequest, Authorship, append_event

    k = lambda: str(uuid.uuid4())                                            # noqa: E731
    text = family["candidate_decision"]
    sections, success = outcome_sections(family, 14, text)
    d = record_decision(session, kp, stream_id=stream, decision_text=text, idempotency_key=k(),
                        actor_kind=ActorKind.PERSON if decision_actor else ActorKind.AGENT,
                        actor_id=decision_actor or HARNESS_AGENT, source=Source.CHAT,
                        authorship=Authorship.SCOPE_PRINCIPAL).envelope
    who = dict(stream_id=stream, actor_kind=ActorKind.AGENT, actor_id=HARNESS_AGENT, source=Source.TOOL,
               authorship=Authorship.SCOPE_PRINCIPAL, idempotency_key=k())
    if link == "decision":
        a = record_action(session, kp, decision_id=d.event_id, action_kind="tool_call", description="apply the change",
                          **who).envelope
    else:
        refs = [] if link == "none" else [{"rel": "execution_of", "event_id": str(link)}]
        a = append_event(session, kp, AppendRequest(event_type=EventType.ACTION, payload_type=PayloadType.STRUCTURED,
                                                    content={"action_kind": "tool_call", "description": "apply the change",
                                                             "dispatched": True, "stakes": [], "refs": refs}, **who)).envelope
    o = record_outcome(session, kp, stream_id=stream, actor_kind=ActorKind.SYSTEM, actor_id=HARNESS_REVIEWER,
                       source=Source.REVIEW, authorship=Authorship.INTEGRATION_RESULT, idempotency_key=k(),
                       outcome_for=a.event_id, success=success, sections=sections).envelope
    return d.event_id, a.event_id, o.event_id
