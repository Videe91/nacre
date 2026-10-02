"""Tests for stores/propose_contradiction.py: action-linked outcomes (D-0020 amendment 1), the judged link's spans and
pins (D-0030), the explicit correction_of trigger's store checks (D-0020), and what stays unchanged."""
import hashlib
import uuid

import pytest

from stores_kit import AGENT, REVIEWER, RULE, episode, grounded_belief
from nacre.capture.record_action import record_action
from nacre.capture.record_correction import record_correction
from nacre.capture.record_decision import record_decision
from nacre.capture.record_outcome import Section, record_outcome
from nacre.core.event import ActorKind, Source
from nacre.ledger.append_event import Authorship
from nacre.ledger.read_stream import read_stream
from nacre.stores.propose_contradiction import ContradictionError, JudgedSpans, propose_contradiction
from nacre.stores.write_version import VersionRecord, read_version_events, write_version

NEW = "Retry VX-41 failures after 5 seconds with header X-Relay: cobalt."
K = lambda: str(uuid.uuid4())                                                    # noqa: E731


def _content(s, kp, a, env):
    (e,) = [e for e in read_stream(s, kp, a) if e.envelope.event_id == env.event_id]
    return e.body["content"]


def _via_action(s, kp, a, correction=NEW, trusted=True):
    d = record_decision(s, kp, stream_id=a, actor_kind=ActorKind.AGENT, actor_id=AGENT, source=Source.CHAT,
                        authorship=Authorship.SCOPE_PRINCIPAL, idempotency_key=K(), decision_text="retry").envelope
    act = record_action(s, kp, stream_id=a, decision_id=d.event_id, action_kind="change", description="apply",
                        actor_kind=ActorKind.AGENT, actor_id=AGENT, source=Source.CHAT,
                        authorship=Authorship.SCOPE_PRINCIPAL, idempotency_key=K()).envelope
    who = (dict(actor_kind=ActorKind.SYSTEM, source=Source.REVIEW, authorship=Authorship.INTEGRATION_RESULT) if trusted
           else dict(actor_kind=ActorKind.TOOL, source=Source.TOOL, authorship=Authorship.EXTERNAL))
    o = record_outcome(s, kp, stream_id=a, actor_id=REVIEWER, idempotency_key=K(), outcome_for=act.event_id,
                       success=False, sections=(Section("status", "FAIL"), Section("correction", correction)),
                       **who).envelope
    return d.event_id, o.event_id


def _head(s, kp, a, oid):
    return [v for v in read_version_events(s, kp, a) if v.body["content"]["object_id"] == str(oid)][-1]


def _judged(s, kp, a, b, d, o, **kw):
    q = "after 5 seconds"
    spans = dict(outcome_id=o, section_index=1, episode_span=(NEW.index(q), NEW.index(q) + len(q)),
                 belief_span=(RULE.index("after 137 ms"), RULE.index("after 137 ms") + 12),
                 judge_result_event_id=uuid.UUID(int=77), head_event_id=_head(s, kp, a, b.object_id).envelope.event_id)
    return propose_contradiction(s, kp, stream_id=a, belief_object_id=b.object_id, decision_id=d,
                                 text=kw.pop("text", q), run_id=uuid.uuid4(), judged=JudgedSpans(**(spans | kw)))


def test_an_outcome_recorded_against_an_action_counts_for_its_decision(rw, provider, streams):
    a = streams["a"]
    with rw() as s:
        b = grounded_belief(s, provider, a)
        d, o = _via_action(s, provider, a)
        env = propose_contradiction(s, provider, stream_id=a, belief_object_id=b.object_id, decision_id=d, text=NEW)
        c = _content(s, provider, a, env)
    assert c["outcome_ids"] == [str(o)] and c["decision_id"] == str(d) and "link" not in c   # direct: body unchanged


def test_a_judged_link_records_both_spans_the_judge_call_and_the_head(rw, provider, streams):
    a = streams["a"]
    with rw() as s:
        b = grounded_belief(s, provider, a)
        d, o = _via_action(s, provider, a)
        env = _judged(s, provider, a, b, d, o)
        c = _content(s, provider, a, env)
        head = _head(s, provider, a, b.object_id)
    sha = lambda t: hashlib.sha256(t.encode()).hexdigest()                        # noqa: E731
    assert (c["link"], c["text"], c["target_event_id"], c["target_version"]) == (
        "judged", "after 5 seconds", str(head.envelope.event_id), 1)
    assert c["episode_span"] == {"outcome_id": str(o), "section_index": 1, "start": NEW.index("after 5"),
                                 "end": NEW.index("after 5") + 15, "sha256": sha("after 5 seconds")}
    assert c["belief_span"] == {"start": RULE.index("after 137"), "end": RULE.index("after 137") + 12,
                                "sha256": sha("after 137 ms")}
    assert c["judge_result_event_id"] == str(uuid.UUID(int=77)) and c["decision_id"] == str(d)


@pytest.mark.parametrize("change,match", [
    (dict(episode_span=(0, 5)), "episode span"),
    (dict(belief_span=(0, 999)), "support_text"),
    (dict(belief_span=(3, 3)), "support_text"),
    (dict(section_index=0, episode_span=(0, 4), text="FAIL"), "authoritative correction"),
    (dict(section_index=7), "section_index"),
    (dict(head_event_id=uuid.uuid4()), "no longer the belief's head"),
    (dict(outcome_id=uuid.uuid4()), "not an outcome of the decision"),
])
def test_a_judged_link_is_refused_unless_both_spans_and_the_head_hold(rw, provider, streams, change, match):
    a = streams["a"]
    with rw() as s:
        b = grounded_belief(s, provider, a)
        d, o = _via_action(s, provider, a)
        with pytest.raises(ContradictionError, match=match):
            _judged(s, provider, a, b, d, o, **change)


def test_a_judged_link_never_uses_an_untrusted_correction(rw, provider, streams):
    a = streams["a"]
    with rw() as s:
        b = grounded_belief(s, provider, a)
        d, o = _via_action(s, provider, a, trusted=False)
        with pytest.raises(ContradictionError, match="authoritative correction"):
            _judged(s, provider, a, b, d, o)


def _supersede_directly(s, kp, a, b):
    """Test-only: write a superseded head (supersession itself is not in scope here)."""
    h = _head(s, kp, a, b.object_id)
    c = h.body["content"]
    write_version(s, kp, a, VersionRecord(b.object_id, c["version"] + 1, "belief", "superseded", c["support"],
                                          c["content"]), carried_from=h.envelope.event_id)


def test_judged_and_explicit_links_never_target_a_superseded_head(rw, provider, streams):
    a = streams["a"]
    with rw() as s:
        b = grounded_belief(s, provider, a)
        version_event = _head(s, provider, a, b.object_id).envelope.event_id
        d, o = _via_action(s, provider, a)
        _supersede_directly(s, provider, a, b)
        with pytest.raises(ContradictionError, match="superseded"):
            _judged(s, provider, a, b, d, o, head_event_id=_head(s, provider, a, b.object_id).envelope.event_id)
        corr = record_correction(s, provider, stream_id=a, actor_kind=ActorKind.PERSON, actor_id=REVIEWER,
                                 source=Source.CHAT, authorship=Authorship.SCOPE_PRINCIPAL, idempotency_key=K(),
                                 correction_of=version_event, text=NEW).envelope
        with pytest.raises(ContradictionError, match="superseded"):
            propose_contradiction(s, provider, stream_id=a, belief_object_id=b.object_id, correction_id=corr.event_id,
                                  text=NEW)


def test_an_explicit_link_needs_a_trusted_correction_naming_a_version_of_that_belief(rw, provider, streams):
    a = streams["a"]
    with rw() as s:
        b = grounded_belief(s, provider, a)
        other = grounded_belief(s, provider, a, correction="Never deploy on Fridays.")
        v = _head(s, provider, a, b.object_id).envelope.event_id

        def corr(target, *, trusted=True, text=NEW):
            who = (dict(actor_kind=ActorKind.PERSON, source=Source.CHAT, authorship=Authorship.SCOPE_PRINCIPAL)
                   if trusted else dict(actor_kind=ActorKind.TOOL, source=Source.TOOL, authorship=Authorship.EXTERNAL))
            return record_correction(s, provider, stream_id=a, actor_id=REVIEWER, idempotency_key=K(),
                                     correction_of=target, text=text, **who).envelope.event_id
        ok = corr(v)
        env = propose_contradiction(s, provider, stream_id=a, belief_object_id=b.object_id, correction_id=ok, text=NEW)
        c = _content(s, provider, a, env)
        assert (c["link"], c["correction_id"], c["decision_id"], c["outcome_ids"], c["text"]) == (
            "explicit", str(ok), None, [], NEW)
        for bad, match in ((corr(v, trusted=False), "trusted"),
                           (corr(_head(s, provider, a, other.object_id).envelope.event_id), "does not name"),
                           (ok, "correction's text")):
            with pytest.raises(ContradictionError, match=match):
                propose_contradiction(s, provider, stream_id=a, belief_object_id=b.object_id, correction_id=bad,
                                      text=NEW if bad != ok else "something else")
        with pytest.raises(ContradictionError, match="not both"):
            propose_contradiction(s, provider, stream_id=a, belief_object_id=b.object_id, correction_id=ok,
                                  decision_id=episode(s, provider, a)[0], text=NEW)
