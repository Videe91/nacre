"""Tests for stores/read_heads.py: as-of reproducibility, withheld statuses, no fallback to older versions, shredding."""
import psycopg

from stores_kit import episode, grounded_belief, propose
from nacre.stores.contest_belief import contest_belief
from nacre.stores.propose_contradiction import propose_contradiction
from nacre.stores.read_heads import read_heads


def test_as_of_reads_history_and_contested_heads_are_withheld_without_falling_back(rw, provider, streams):
    a = streams["a"]
    with rw() as s:
        b = grounded_belief(s, provider, a)
        seq_v1 = max(h.commit_seq for h in read_heads(s, provider, a))
        for _ in range(2):
            d, _o = episode(s, provider, a, correction="never retry")
            propose_contradiction(s, provider, stream_id=a, belief_object_id=b.object_id, decision_id=d, text="never retry")
        contest_belief(s, provider, a, b.object_id)
        assert read_heads(s, provider, a) == []                                         # no older active version shown
        assert [h.status for h in read_heads(s, provider, a, include_inactive=True)] == ["contested"]
        (old,) = read_heads(s, provider, a, as_of=seq_v1)
        assert (old.version, old.status) == (1, "active")                               # history at watermark N


def test_a_shredded_head_keeps_its_structure_but_not_its_content(rw, provider, streams):
    a = streams["a"]
    with rw() as s:
        grounded_belief(s, provider, a)
    with psycopg.connect(streams["dsn"]["admin"]) as c:
        c.execute("DELETE FROM keys.stream_master_keys WHERE stream_id = %s", (a,))
    with rw() as s:
        assert read_heads(s, provider, a) == []                                      # lost: not recall-eligible
        (h,) = read_heads(s, provider, a, include_unreadable=True)
    assert (h.kind, h.status, h.content) == ("belief", "active", None)
