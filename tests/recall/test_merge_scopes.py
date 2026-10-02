"""Tests for recall/merge_scopes.py (R13, D-0025 §2 + amendment 1): the eligibility rule, contested heads included with
their contradicting evidence, superseded and lost heads excluded, scope ranking, snapshot binding; read_heads keeps
D-0017's rule."""
import uuid

import psycopg
import pytest

from recall_kit import belief
from nacre.recall.freeze_snapshot import freeze_snapshot
from nacre.recall.merge_scopes import Candidate, MergeRefused, merge_scopes
from nacre.stores.contest_belief import contest_belief
from nacre.stores.propose_contradiction import propose_contradiction
from nacre.stores.read_heads import read_heads


def _contest(s, provider, stream, object_id):
    """Two independent agreeing contradictions (D-0017 quorum) make the head contested."""
    from recall_kit import counter_episode
    for _ in range(2):
        d = counter_episode(s, provider, stream, "Never pin the payments image.")
        propose_contradiction(s, provider, stream_id=stream, belief_object_id=object_id, decision_id=d,
                              text="never pin the payments image")
    return contest_belief(s, provider, stream, object_id)


def _pool(snap, scopes):
    with snap() as s:
        return merge_scopes(s, freeze_snapshot(s, [st for _, st in scopes]), scopes)


def test_active_and_contested_heads_are_eligible_and_contested_carries_its_evidence(rw, snap, provider, streams):
    a = streams["a"]
    with rw() as s:
        keep = belief(s, provider, a, "Run the schema migration check before merging 2026.", "Run the schema migration check")
        disputed = belief(s, provider, a, "Pin the payments base image by digest 2026.", "Pin the payments base image")
        assert _contest(s, provider, a, disputed.object_id).status == "contested"
    pool = {c.object_id: c for c in _pool(snap, [("project", a)])}
    assert pool[keep.object_id].status == "active" and pool[keep.object_id].contradicting_event_ids == ()
    c = pool[disputed.object_id]
    assert c.contested and c.status == "contested" and len(c.contradicting_event_ids) == 2
    with rw() as s:
        n = s.conn.execute("SELECT count(*) FROM ledger.events WHERE event_id = ANY(%s) AND event_type = 'memory_event'",
                           (list(c.contradicting_event_ids),)).fetchone()[0]
    assert n == 2                                            # the evidence: the two agreeing contradiction proposals


def test_read_heads_keeps_d0017_and_still_withholds_the_contested_head(rw, provider, streams):
    a = streams["a"]
    with rw() as s:
        disputed = belief(s, provider, a, "Pin the payments base image by digest 2026.", "Pin the payments base image")
        _contest(s, provider, a, disputed.object_id)
        assert disputed.object_id not in {h.object_id for h in read_heads(s, provider, a)}


def test_superseded_and_lost_heads_are_excluded(rw, snap, provider, streams, migrated_db):
    a = streams["a"]
    p = uuid.UUID(int=77)
    with rw() as s:
        lost = belief(s, provider, a, "Rotate the staging certificate weekly 2026.", "Rotate the staging certificate", person=p)
        kept = belief(s, provider, a, "Run the schema migration check before merging 2026.", "Run the schema migration check")
        key = s.conn.execute("SELECT e.key_id FROM interp.versions v JOIN ledger.events e ON e.event_id = v.event_id "
                             "WHERE v.object_id = %s", (lost.object_id,)).fetchone()[0]
    with psycopg.connect(migrated_db["keyadmin"]) as ka:
        ka.execute("DELETE FROM keys.data_keys WHERE key_id = %s", (key,))
    ids = {c.object_id for c in _pool(snap, [("project", a)])}
    assert kept.object_id in ids and lost.object_id not in ids


def test_only_eligible_statuses_reach_the_pool(rw, snap, provider, streams):
    from nacre.recall.merge_scopes import ELIGIBLE_STATUSES
    assert set(ELIGIBLE_STATUSES) == {"active", "contested", "fallback"}       # superseded is never eligible
    a = streams["a"]
    with rw() as s:
        belief(s, provider, a, "Run the schema migration check before merging 2026.", "Run the schema migration check")
    assert all(c.status in ELIGIBLE_STATUSES for c in _pool(snap, [("project", a)]))


def test_scope_levels_rank_narrowest_first(rw, snap, grant, principal, session, provider, streams):
    a, b = streams["a"], streams["b"]
    with rw() as s:
        belief(s, provider, a, "Run the schema migration check before merging 2026.", "Run the schema migration check")
    with session(uuid.uuid4(), read=[b], write=[b]) as s:
        belief(s, provider, b, "Pin the payments base image by digest 2026.", "Pin the payments base image")
    grant(principal, b, append=False)
    pool = _pool(snap, [("task", b), ("project", a)])
    assert {(c.stream_id, c.scope_level, c.level_rank) for c in pool} == {(b, "task", 0), (a, "project", 1)}


def test_streams_outside_the_snapshot_are_refused(rw, snap, provider, streams):
    with rw() as s:
        s.conn.execute("SELECT 1")
    with snap() as s:
        snapshot = freeze_snapshot(s, [streams["a"]])
        with pytest.raises(MergeRefused):
            merge_scopes(s, snapshot, [("project", streams["a"]), ("team", streams["b"])])


def test_a_head_committed_after_the_snapshot_position_is_not_a_candidate(rw, snap, provider, streams):
    a = streams["a"]
    with rw() as s:
        belief(s, provider, a, "Run the schema migration check before merging 2026.", "Run the schema migration check")
    with snap() as s:
        snapshot = freeze_snapshot(s, [a])
        with rw() as w:                                        # another connection commits a new belief meanwhile
            belief(w, provider, a, "Pin the payments base image by digest 2026.", "Pin the payments base image")
        pool = merge_scopes(s, snapshot, [("project", a)])
    assert len(pool) == 1 and isinstance(pool[0], Candidate)
