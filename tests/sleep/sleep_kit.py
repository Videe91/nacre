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


# ---- D-0030: synthetic changing facts (a T3 family) and a purpose-aware fake ----

ADDR = "code:svc/src/catalog/parser.py"
V1 = "Schema changes behind parser.py need a 1750 ms lock_timeout, because writes pile up during imports."
V2A = "Lock timeout change for parser.py: migrations now set 100 ms; the previous value stalled writes."
V2B = "Migrations behind parser.py use a 100 ms lock_timeout from now on; the old setting failed under reports."


def t3_episode(session, kp, stream, correction, *, addresses=(ADDR,), via_action=False, trusted=True,
               extra=(), decision_id=None):
    """decision (-> action) -> outcome whose sections are (status FAIL, correction, *extra). trusted: a reviewer
    person (review); else a tool (external, untrusted). decision_id: reuse an existing decision (a second action of it).
    Returns (decision_id, action_id | None, outcome_id)."""
    import uuid

    from nacre.capture.record_action import record_action
    from nacre.capture.record_decision import record_decision
    from nacre.capture.record_outcome import Section, record_outcome
    from nacre.core.event import ActorKind, Source
    from nacre.ledger.append_event import Authorship

    k = lambda: str(uuid.uuid4())                                            # noqa: E731
    agent = uuid.UUID(int=21)
    if decision_id is None:
        decision_id = record_decision(session, kp, stream_id=stream, actor_kind=ActorKind.AGENT, actor_id=agent,
                                      source=Source.CHAT, authorship=Authorship.SCOPE_PRINCIPAL, idempotency_key=k(),
                                      decision_text="Keep the lock timeout of parser.py as it is.",
                                      addresses=addresses).envelope.event_id
    target, action = decision_id, None
    if via_action:
        action = record_action(session, kp, stream_id=stream, decision_id=decision_id, action_kind="change",
                               description="apply the migration", actor_kind=ActorKind.AGENT, actor_id=agent,
                               source=Source.CHAT, authorship=Authorship.SCOPE_PRINCIPAL, idempotency_key=k(),
                               addresses=addresses).envelope.event_id
        target = action
    who = (dict(actor_kind=ActorKind.PERSON, actor_id=uuid.UUID(int=22), source=Source.REVIEW,
                authorship=Authorship.SCOPE_PRINCIPAL) if trusted
           else dict(actor_kind=ActorKind.TOOL, actor_id=uuid.UUID(int=23), source=Source.TOOL,
                     authorship=Authorship.EXTERNAL))
    o = record_outcome(session, kp, stream_id=stream, idempotency_key=k(), outcome_for=target, success=False,
                       sections=(Section("status", "FAIL"), Section("correction", correction),
                                 *(Section(r, t) for r, t in extra)), addresses=addresses, **who).envelope.event_id
    return decision_id, action, o


def belief(session, kp, stream, correction, **kw):
    """A single-source belief from a trusted correction (no model): returns its Promotion."""
    from nacre.stores.promote_if_supported import promote_if_supported
    from nacre.stores.propose_lesson import propose_lesson
    d, _, o = t3_episode(session, kp, stream, correction, **kw)
    pid = propose_lesson(session, kp, stream_id=stream, decision_id=d, outcome_id=o, section_index=1,
                         span=(0, len(correction)), nucleus=correction).event_id
    return promote_if_supported(session, kp, stream, pid)


def verdict(label, relation="unrelated", section=-1, episode_quote="", belief_quote=""):
    return {"belief": label, "relation": relation, "section": section, "episode_quote": episode_quote,
            "belief_quote": belief_quote}


@dataclass
class Smart:
    """Proposer/repair: quote every AUTHORITATIVE correction section whole (nucleus = the quote). Judge: `judge`
    (corrections {N: text}, beliefs {"Bn": text}) -> list of verdict dicts; default: every belief unrelated."""
    judge: object = None
    name: str = "openai"
    replay: bool = False
    requests: list = field(default_factory=list)

    def complete(self, request, *, timeout_s):
        import re
        self.requests.append(request)
        text = request.messages[0].content
        if request.purpose == "sleep.judge_relations":
            corr = {int(n): t for n, t in re.findall(r"^\[S(\d+)\]\n(.*)$", text, re.M)}
            beliefs = dict(re.findall(r"^\[(B\d+)\]\n(.*)$", text, re.M))
            items = (self.judge or (lambda c, b: [verdict(x) for x in b]))(corr, beliefs)
            body = json.dumps({"verdicts": items})
        else:
            quotes = re.findall(r"^\[S(\d+)\] role=correction \(AUTHORITATIVE\)\n(.*)$", text, re.M)
            body = props(*((int(n), q, q) for n, q in quotes))
        return ModelResponse(text=body, finish_reason="completed", usage=Usage(1000, 200, None), response_id="r",
                             model_reported=MODEL, latency_ms=5)

    def purposes(self):
        return [r.purpose for r in self.requests]


def contradicting(old_text, old_quote, new_quote):
    """A judge that links every belief whose text is `old_text`, quoting `new_quote` from the first correction that
    contains it and `old_quote` from the belief."""
    def judge(corr, beliefs):
        sec = next((n for n, t in corr.items() if new_quote in t), -1)
        return [verdict(b, "contradicts", sec, new_quote, old_quote) if t == old_text else verdict(b)
                for b, t in beliefs.items()]
    return judge
