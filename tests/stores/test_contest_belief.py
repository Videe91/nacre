"""Tests for stores/propose_contradiction.py and stores/contest_belief.py (MNEXA ledger 38 rules + deviation 3)."""
import pytest

from stores_kit import episode, grounded_belief
from nacre.stores.contest_belief import contest_belief
from nacre.stores.propose_contradiction import ContradictionError, propose_contradiction


def _contradict(s, provider, a, oid, text="never retry VX-41", decision=None):
    d = decision or episode(s, provider, a, correction=text)[0]
    propose_contradiction(s, provider, stream_id=a, belief_object_id=oid, decision_id=d, text=text)
    return d


def test_one_contradiction_does_not_contest_two_independent_ones_do(rw, provider, streams):
    a = streams["a"]
    with rw() as s:
        b = grounded_belief(s, provider, a)
        _contradict(s, provider, a, b.object_id)
        assert contest_belief(s, provider, a, b.object_id).status == "active"
        _contradict(s, provider, a, b.object_id, text="NEVER  retry vx-41")          # normalised: same claim
        r = contest_belief(s, provider, a, b.object_id)
    assert (r.status, r.version, r.created) == ("contested", 2, True)


def test_same_decision_counts_once_and_different_claims_do_not_combine(rw, provider, streams):
    a = streams["a"]
    with rw() as s:
        b = grounded_belief(s, provider, a)
        d = _contradict(s, provider, a, b.object_id)
        _contradict(s, provider, a, b.object_id, decision=d)
        _contradict(s, provider, a, b.object_id, text="retry after 5 seconds instead")
        assert contest_belief(s, provider, a, b.object_id).status == "active"


def test_contradictions_are_pinned_to_the_exact_head_version(rw, provider, streams):
    from stores_kit import propose
    from nacre.stores.promote_if_supported import promote_if_supported
    a = streams["a"]
    with rw() as s:
        b = grounded_belief(s, provider, a)
        _contradict(s, provider, a, b.object_id)                                      # pinned to v1
        d, o = episode(s, provider, a, trusted=False)
        promote_if_supported(s, provider, a, propose(s, provider, a, d, o))           # head is now v2
        _contradict(s, provider, a, b.object_id)                                      # pinned to v2
        assert contest_belief(s, provider, a, b.object_id).status == "active"         # v1's proposal does not count


def test_deviation3_contest_is_a_no_op_on_contested_or_superseded(rw, provider, streams):
    a = streams["a"]
    with rw() as s:
        b = grounded_belief(s, provider, a)
        _contradict(s, provider, a, b.object_id)
        _contradict(s, provider, a, b.object_id)
        first = contest_belief(s, provider, a, b.object_id)
        again = contest_belief(s, provider, a, b.object_id)
    assert (again.version, again.created) == (first.version, False)


def test_a_contradiction_needs_a_decision_with_an_outcome(rw, provider, streams):
    from stores_kit import AGENT
    import uuid
    from nacre.capture.record_decision import record_decision
    from nacre.core.event import ActorKind, Source
    from nacre.ledger.append_event import Authorship
    a = streams["a"]
    with rw() as s:
        b = grounded_belief(s, provider, a)
        lonely = record_decision(s, provider, stream_id=a, actor_kind=ActorKind.AGENT, actor_id=AGENT, source=Source.CHAT,
                                 authorship=Authorship.SCOPE_PRINCIPAL, idempotency_key=str(uuid.uuid4()),
                                 decision_text="x").envelope.event_id
        with pytest.raises(ContradictionError, match="outcome"):
            propose_contradiction(s, provider, stream_id=a, belief_object_id=b.object_id, decision_id=lonely, text="no")
        with pytest.raises(ContradictionError, match="empty"):
            propose_contradiction(s, provider, stream_id=a, belief_object_id=b.object_id, decision_id=lonely, text=" ")


def test_deviation3_holds_even_when_new_contradictions_target_the_contested_version(rw, provider, streams):
    a = streams["a"]
    with rw() as s:
        b = grounded_belief(s, provider, a)
        for _ in range(2):
            _contradict(s, provider, a, b.object_id)
        contested = contest_belief(s, provider, a, b.object_id)
        for _ in range(2):                                     # now pinned to the contested head
            _contradict(s, provider, a, b.object_id, text="still never retry")
        again = contest_belief(s, provider, a, b.object_id)
    assert (again.version, again.created, again.status) == (contested.version, False, "contested")


# ---- D-0030 owner condition 1: judged links are grouped by target head; two DISTINCT decisions contest ----

def _judged_link(s, provider, a, oid, text, decision=None):
    """A judged link quoting `text` (the whole correction section) against the head's whole support_text."""
    import uuid

    from nacre.stores.propose_contradiction import JudgedSpans
    from nacre.stores.write_version import read_version_events
    d, o = (decision, None) if decision else episode(s, provider, a, correction=text)
    if o is None:
        from stores_kit import REVIEWER
        from nacre.capture.record_outcome import Section, record_outcome
        from nacre.core.event import ActorKind, Source
        from nacre.ledger.append_event import Authorship
        o = record_outcome(s, provider, stream_id=a, actor_kind=ActorKind.SYSTEM, actor_id=REVIEWER, source=Source.REVIEW,
                           authorship=Authorship.INTEGRATION_RESULT, idempotency_key=str(uuid.uuid4()), outcome_for=d,
                           success=False, sections=(Section("status", "FAIL"), Section("correction", text))).envelope.event_id
    head = [v for v in read_version_events(s, provider, a) if v.body["content"]["object_id"] == str(oid)][-1]
    support = head.body["content"]["content"]["support_text"]
    return propose_contradiction(s, provider, stream_id=a, belief_object_id=oid, decision_id=d, text=text,
                                 judged=JudgedSpans(o, 1, (0, len(text)), (0, len(support)), uuid.uuid4(),
                                                    head.envelope.event_id)), d


def test_one_judged_link_does_not_contest(rw, provider, streams):
    a = streams["a"]
    with rw() as s:
        b = grounded_belief(s, provider, a)
        _judged_link(s, provider, a, b.object_id, "Retry VX-41 after 5 s.")
        assert contest_belief(s, provider, a, b.object_id).status == "active"


def test_two_judged_links_from_the_same_decision_do_not_contest(rw, provider, streams):
    a = streams["a"]
    with rw() as s:
        b = grounded_belief(s, provider, a)
        _, d = _judged_link(s, provider, a, b.object_id, "Retry VX-41 after 5 s.")
        _judged_link(s, provider, a, b.object_id, "Wait 5 seconds before a VX-41 retry.", decision=d)
        assert contest_belief(s, provider, a, b.object_id).status == "active"


def test_two_paraphrased_judged_links_from_distinct_decisions_contest(rw, provider, streams):
    a = streams["a"]
    with rw() as s:
        b = grounded_belief(s, provider, a)
        e1, d1 = _judged_link(s, provider, a, b.object_id, "Retry VX-41 after 5 s.")
        e2, d2 = _judged_link(s, provider, a, b.object_id, "Wait 5 seconds before a VX-41 retry.")
        r = contest_belief(s, provider, a, b.object_id)
        from nacre.stores.write_version import read_version_events
        head = read_version_events(s, provider, a)[-1].body["content"]
    assert (r.status, r.version, r.created) == ("contested", 2, True)
    assert head["content"]["contradiction_decisions"] == sorted([str(d1), str(d2)])
    assert {x["target_event_id"] for x in head["edges"] if x["role"] == "contradiction"} == {
        str(e1.event_id), str(e2.event_id)}                         # each link's identity (D-0030 S2 seam)


def test_a_judged_and_a_text_proposal_never_combine_and_explicit_links_never_vote(rw, provider, streams):
    import uuid

    from stores_kit import REVIEWER
    from nacre.capture.record_correction import record_correction
    from nacre.core.event import ActorKind, Source
    from nacre.ledger.append_event import Authorship
    from nacre.stores.write_version import read_version_events
    a = streams["a"]
    text = "never retry VX-41"
    with rw() as s:
        b = grounded_belief(s, provider, a)
        _judged_link(s, provider, a, b.object_id, text)
        _contradict(s, provider, a, b.object_id, text=text)                  # same text, but a direct proposal
        v = [x for x in read_version_events(s, provider, a)][-1].envelope.event_id
        for _ in range(2):
            c = record_correction(s, provider, stream_id=a, actor_kind=ActorKind.PERSON, actor_id=REVIEWER,
                                  source=Source.CHAT, authorship=Authorship.SCOPE_PRINCIPAL,
                                  idempotency_key=str(uuid.uuid4()), correction_of=v, text=text).envelope.event_id
            propose_contradiction(s, provider, stream_id=a, belief_object_id=b.object_id, correction_id=c, text=text)
        assert contest_belief(s, provider, a, b.object_id).status == "active"
