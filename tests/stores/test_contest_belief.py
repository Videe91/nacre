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
