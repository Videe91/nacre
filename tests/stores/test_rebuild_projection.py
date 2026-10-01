"""Tests for stores/rebuild_projection.py (gate item 8): recomputed projection = stored projection."""
import uuid

import psycopg

from stores_kit import episode, grounded_belief, propose
from nacre.stores.contest_belief import contest_belief
from nacre.stores.promote_if_supported import promote_if_supported
from nacre.stores.propose_contradiction import propose_contradiction
from nacre.stores.rebuild_projection import rebuild_projection


def _workflow(s, provider, a):
    b = grounded_belief(s, provider, a)
    d, o = episode(s, provider, a, trusted=False)
    promote_if_supported(s, provider, a, propose(s, provider, a, d, o))
    for _ in range(2):
        d, _o = episode(s, provider, a, correction="never")
        propose_contradiction(s, provider, stream_id=a, belief_object_id=b.object_id, decision_id=d, text="never")
    contest_belief(s, provider, a, b.object_id)
    d, o = episode(s, provider, a)
    promote_if_supported(s, provider, a, propose(s, provider, a, d, o, nucleus=None))     # a fallback too


def test_the_recomputed_projection_is_identical(rw, provider, streams):
    with rw() as s:
        _workflow(s, provider, streams["a"])
        check = rebuild_projection(s, provider, streams["a"])
    assert check.identical and check.expected_versions == 4 and check.expected_edges > 6


def test_a_stored_row_without_its_event_is_reported(rw, provider, streams):
    a = streams["a"]
    with rw() as s:
        _workflow(s, provider, a)
    with psycopg.connect(streams["dsn"]["admin"]) as c:                 # a DB-level forger adds a row
        c.execute("ALTER TABLE interp.versions DISABLE TRIGGER versions_check")
        row = c.execute("SELECT event_id, commit_seq FROM interp.versions LIMIT 1").fetchone()
        c.execute("INSERT INTO interp.versions VALUES (%s,1,'belief','active','quorum',%s,%s,%s,%s)",
                  (uuid.uuid4(), a, uuid.uuid4(), row[1], b"\x00" * 32))
        c.execute("ALTER TABLE interp.versions ENABLE TRIGGER versions_check")
    with rw() as s:
        check = rebuild_projection(s, provider, a)
    assert not check.identical and len(check.differences) == 1


def test_shredded_versions_are_unverifiable_not_different(rw, provider, streams):
    a = streams["a"]
    with rw() as s:
        _workflow(s, provider, a)
    with psycopg.connect(streams["dsn"]["admin"]) as c:
        c.execute("DELETE FROM keys.stream_master_keys WHERE stream_id = %s", (a,))
    with rw() as s:
        check = rebuild_projection(s, provider, a)
    assert check.identical and check.unverifiable
