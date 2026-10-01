"""Tests for stores/supersede_belief.py (MNEXA ledger 39 rules)."""
import pytest

from stores_kit import episode, grounded_belief, propose
from nacre.stores.contest_belief import contest_belief
from nacre.stores.promote_if_supported import promote_if_supported
from nacre.stores.propose_contradiction import propose_contradiction
from nacre.stores.read_heads import read_heads
from nacre.stores.supersede_belief import SupersessionError, supersede_belief

NEW = "Never retry VX-41; page the on-call engineer."


def _counter_episodes(s, provider, a, old_oid, n=2):
    """n decisions whose outcomes contradict the old belief AND support the replacement."""
    rep = None
    for _ in range(n):
        d, o = episode(s, provider, a, correction=NEW)
        propose_contradiction(s, provider, stream_id=a, belief_object_id=old_oid, decision_id=d, text="never retry VX-41")
        rep = promote_if_supported(s, provider, a, propose(s, provider, a, d, o, text=NEW, nucleus="Never retry VX-41"))
    return rep


def test_active_beliefs_cannot_be_superseded(rw, provider, streams):
    a = streams["a"]
    with rw() as s:
        old = grounded_belief(s, provider, a)
        rep = _counter_episodes(s, provider, a, old.object_id)
        with pytest.raises(SupersessionError, match="contested"):
            supersede_belief(s, provider, a, old.object_id, rep.object_id)


def test_two_shared_counter_decisions_supersede_and_recall_shows_the_replacement(rw, provider, streams):
    a = streams["a"]
    with rw() as s:
        old = grounded_belief(s, provider, a)
        rep = _counter_episodes(s, provider, a, old.object_id)
        contest_belief(s, provider, a, old.object_id)
        r = supersede_belief(s, provider, a, old.object_id, rep.object_id)
        visible = {h.object_id for h in read_heads(s, provider, a)}
        again = supersede_belief(s, provider, a, old.object_id, rep.object_id)
    assert r.status == "superseded" and visible == {rep.object_id}
    assert (again.version, again.created) == (r.version, False)


def test_one_shared_decision_is_not_enough(rw, provider, streams):
    a = streams["a"]
    with rw() as s:
        old = grounded_belief(s, provider, a)
        d, _o = episode(s, provider, a, correction="never retry")
        propose_contradiction(s, provider, stream_id=a, belief_object_id=old.object_id, decision_id=d, text="never retry VX-41")
        rep = _counter_episodes(s, provider, a, old.object_id, n=1)
        contest_belief(s, provider, a, old.object_id)
        assert supersede_belief(s, provider, a, old.object_id, rep.object_id) is None


def test_same_proposition_or_another_replacement_is_refused(rw, provider, streams):
    a = streams["a"]
    with rw() as s:
        old = grounded_belief(s, provider, a)
        rep = _counter_episodes(s, provider, a, old.object_id)
        contest_belief(s, provider, a, old.object_id)
        with pytest.raises(SupersessionError, match="different proposition"):
            supersede_belief(s, provider, a, old.object_id, old.object_id)
        supersede_belief(s, provider, a, old.object_id, rep.object_id)
        other = grounded_belief(s, provider, a, correction="Use exponential backoff for VX-41.")
        with pytest.raises(SupersessionError, match="different replacement"):
            supersede_belief(s, provider, a, old.object_id, other.object_id)


def test_a_contested_replacement_cannot_supersede(rw, provider, streams):
    a = streams["a"]
    with rw() as s:
        old = grounded_belief(s, provider, a)
        rep = _counter_episodes(s, provider, a, old.object_id)
        contest_belief(s, provider, a, old.object_id)
        for _ in range(2):
            d, _o = episode(s, provider, a, correction="no")
            propose_contradiction(s, provider, stream_id=a, belief_object_id=rep.object_id, decision_id=d, text="no")
        assert contest_belief(s, provider, a, rep.object_id).status == "contested"
        assert supersede_belief(s, provider, a, old.object_id, rep.object_id) is None
