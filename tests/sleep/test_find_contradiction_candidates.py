"""Tests for sleep/find_contradiction_candidates.py: same stream, a shared identity address, never superseded, K and
the fixed order (D-0030 owner condition 2)."""
import uuid

from sleep_kit import ADDR, V1, belief, t3_episode
from nacre.ledger.read_stream import read_stream
from nacre.sleep.find_contradiction_candidates import (K, BeliefHead, episode_addresses, find_candidates,
                                                       read_belief_heads)


def _head(n, *, status="active", addresses=(ADDR,), seq=None, stream=None):
    return BeliefHead(stream or uuid.UUID(int=1), uuid.UUID(int=100 + n), 1, status, uuid.uuid4(),
                      seq if seq is not None else n, f"belief {n}", frozenset(addresses))


def test_only_shared_identity_addresses_make_a_candidate():
    heads = {h.object_id: h for h in (
        _head(1), _head(2, addresses=("code:other.py",)), _head(3, addresses=()),
        _head(4, addresses=("system:" + ADDR[5:],)))}                       # a non-identity type never matches
    assert [c.head.object_id for c in find_candidates(heads, frozenset({ADDR}))] == [uuid.UUID(int=101)]
    assert find_candidates(heads, frozenset()) == []


def test_superseded_heads_are_never_candidates_contested_ones_are():
    heads = {h.object_id: h for h in (_head(1, status="superseded"), _head(2, status="contested"), _head(3))}
    assert [c.head.status for c in find_candidates(heads, frozenset({ADDR}))] == ["active", "contested"]


def test_order_is_most_shared_then_most_recent_then_object_id_and_at_most_k():
    two = (ADDR, "entity:catalog")
    heads = [_head(1, seq=50), _head(2, addresses=two, seq=1), _head(3, seq=50), _head(4, seq=60)]
    heads += [_head(n, seq=n) for n in range(5, 12)]
    got = find_candidates({h.object_id: h for h in heads}, frozenset(two))
    assert len(got) == K == 5
    assert [c.head.object_id.int - 100 for c in got] == [2, 4, 1, 3, 11]       # 1 and 3 tie on seq: object id
    got = find_candidates({h.object_id: h for h in heads[:4]}, frozenset(two))
    assert [c.head.object_id.int - 100 for c in got] == [2, 4, 1, 3] and got[0].shared == tuple(sorted(two))


def test_heads_and_addresses_come_from_this_stream_only(world, provider):
    a, b = world["proj"], world["new_stream"]()
    with world["session"]() as s:
        belief(s, provider, b, V1)                                   # another stream, same address
        d, _, o = t3_episode(s, provider, a, "Use 100 ms.", via_action=True, addresses=(ADDR, "system:db"))
    with world["session"]() as s:
        assert read_belief_heads(s, provider, a) == {}
        (h,) = read_belief_heads(s, provider, b).values()
        index = {e.envelope.event_id: e for e in read_stream(s, provider, a)}
    assert h.stream_id == b and h.addresses == frozenset({ADDR}) and h.support_text == V1 and h.status == "active"
    assert episode_addresses(index, [d, o]) == frozenset({ADDR})      # identity types only
    assert episode_addresses(index, [uuid.uuid4()]) == frozenset()
