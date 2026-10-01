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


def _forge_extra_row(streams, a):
    with psycopg.connect(streams["dsn"]["admin"]) as c:
        c.execute("ALTER TABLE interp.versions DISABLE TRIGGER versions_check")
        row = c.execute("SELECT commit_seq FROM interp.versions LIMIT 1").fetchone()
        forged = uuid.uuid4()
        c.execute("INSERT INTO interp.versions (object_id, version, kind, status, support, stream_id, event_id, commit_seq, "
                  "content_mac) VALUES (%s,1,'belief','active','quorum',%s,%s,%s,%s)", (forged, a, uuid.uuid4(), row[0], b"\x00" * 32))
        c.execute("ALTER TABLE interp.versions ENABLE TRIGGER versions_check")
    return forged


def test_a_mismatch_switches_to_a_new_generation_and_keeps_the_old(rw, provider, streams):
    from nacre.stores.read_heads import read_heads
    a = streams["a"]
    with rw() as s:
        _workflow(s, provider, a)
    forged = _forge_extra_row(streams, a)
    with rw() as s:
        assert forged in {h.object_id for h in read_heads(s, provider, a, include_inactive=True)}    # served from gen 1
        check = rebuild_projection(s, provider, a)
    assert (check.generation, check.switched_to, len(check.differences)) == (1, 2, 1)
    with rw() as s:
        assert forged not in {h.object_id for h in read_heads(s, provider, a, include_inactive=True)}  # gen 2 is clean
        gens = dict(s.conn.execute("SELECT generation, count(*) FROM interp.versions WHERE stream_id = %s GROUP BY 1", (a,)).fetchall())
        assert gens[1] == gens[2] + 1                                                          # gen 1 retained, forged row incl.
        again = rebuild_projection(s, provider, a)
        assert again.identical and again.generation == 2 and again.switched_to is None
        assert s.conn.execute("SELECT count(*) FROM interp.generation_switches WHERE stream_id = %s", (a,)).fetchone()[0] == 1


def test_an_identical_rebuild_writes_nothing(rw, provider, streams):
    a = streams["a"]
    with rw() as s:
        _workflow(s, provider, a)
        before = s.conn.execute("SELECT count(*) FROM interp.versions").fetchone()[0]
        assert rebuild_projection(s, provider, a).switched_to is None
        assert s.conn.execute("SELECT count(*) FROM interp.versions").fetchone()[0] == before


def test_writes_after_a_switch_go_into_the_new_generation(rw, provider, streams):
    a = streams["a"]
    with rw() as s:
        _workflow(s, provider, a)
    _forge_extra_row(streams, a)
    with rw() as s:
        rebuild_projection(s, provider, a)
        b = grounded_belief(s, provider, a, correction="Rotate the staging certificate every 30 days.")
        gen = s.conn.execute("SELECT generation FROM interp.versions WHERE object_id = %s", (b.object_id,)).fetchall()
        assert gen == [(2,)] and rebuild_projection(s, provider, a).identical


def test_an_aborted_rebuild_leaves_no_new_generation(rw, provider, streams):
    a = streams["a"]
    with rw() as s:
        _workflow(s, provider, a)
    _forge_extra_row(streams, a)
    import pytest
    with pytest.raises(RuntimeError):
        with rw() as s:
            rebuild_projection(s, provider, a)
            raise RuntimeError("crash before commit")
    with rw() as s:
        assert s.conn.execute("SELECT interp.active_generation(%s)", (a,)).fetchone()[0] == 1
        assert s.conn.execute("SELECT count(*) FROM interp.versions WHERE generation = 2").fetchone()[0] == 0


def test_shredded_rows_are_carried_into_the_new_generation(rw, provider, streams):
    a = streams["a"]
    with rw() as s:
        _workflow(s, provider, a)
    forged = _forge_extra_row(streams, a)
    with psycopg.connect(streams["dsn"]["admin"]) as c:
        c.execute("DELETE FROM keys.stream_master_keys WHERE stream_id = %s", (a,))
    with rw() as s:
        check = rebuild_projection(s, provider, a)
        n1, n2 = [s.conn.execute("SELECT count(*) FROM interp.versions WHERE stream_id = %s AND generation = %s", (a, g)).fetchone()[0]
                  for g in (1, 2)]
    assert check.unverifiable and check.switched_to == 2 and n2 == n1 - 1 and forged


def test_switches_must_be_to_the_next_non_empty_generation_and_are_append_only(rw, provider, streams):
    import pytest
    a = streams["a"]
    with rw() as s:
        _workflow(s, provider, a)
    for gen in (3, 2):                                              # skips ahead; next but empty
        with pytest.raises(psycopg.errors.RaiseException):
            with rw() as s:
                s.conn.execute("INSERT INTO interp.generation_switches (stream_id, generation, reason, differences) "
                               "VALUES (%s, %s, 'x', 1)", (a, gen))
    _forge_extra_row(streams, a)
    with rw() as s:
        rebuild_projection(s, provider, a)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with rw() as s:
            s.conn.execute("DELETE FROM interp.generation_switches WHERE stream_id = %s", (a,))
