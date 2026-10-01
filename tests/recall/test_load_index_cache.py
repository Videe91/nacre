"""Tests for recall/load_index_cache.py (R10, D-0024 owner decision 2): the cache serves only inside a snapshot whose
grants were confirmed in that snapshot; erasure (in every process) and grant revocation take effect by the next
recall; a generation or embedder change reloads; the memory cap evicts least recently used streams."""
import uuid

import psycopg
import pytest

from recall_kit import belief
import nacre.recall.load_index_cache as cache_mod
from nacre.recall.embed_local import DIM
from nacre.recall.load_index_cache import CacheRefused, IndexCache

P, Q = uuid.UUID(int=901), uuid.UUID(int=902)


def _heads(s, stream):
    return {r[0] for r in s.conn.execute("SELECT event_id FROM interp.heads WHERE stream_id = %s", (stream,))}


def _key_of(s, vid):
    return s.conn.execute("SELECT key_id FROM ledger.events WHERE event_id = %s", (vid,)).fetchone()[0]


def _erase_key(migrated_db, stream, key_id):
    """What execute_due_shreds does in one keyadmin transaction: destroy the key and bump the stream's epoch."""
    with psycopg.connect(migrated_db["keyadmin"]) as ka:
        ka.execute("DELETE FROM keys.data_keys WHERE key_id = %s", (key_id,))
        ka.execute("INSERT INTO keys.shred_epochs (stream_id, epoch) VALUES (%s, 1) "
                   "ON CONFLICT (stream_id) DO UPDATE SET epoch = keys.shred_epochs.epoch + 1", (stream,))


@pytest.fixture
def two(rw, provider, streams):
    """Two beliefs in stream a under different contributor-set keys (reviewers P and Q)."""
    with rw() as s:
        belief(s, provider, streams["a"], "Pin the payments base image by digest before release.",
               "Pin the payments base image by digest", person=P)
        belief(s, provider, streams["a"], "Run the schema migration check before merging.", "Run the schema migration check",
               person=Q)
    with rw() as s:
        heads = sorted(_heads(s, streams["a"]))
        keys = {v: _key_of(s, v) for v in heads}
    assert len(set(keys.values())) == 2
    return heads, keys


def test_cold_then_warm_serves_the_same_entries_and_decrypts_once(two, snap, provider, streams, monkeypatch):
    heads, _ = two
    calls = []
    real = cache_mod.open_entry
    monkeypatch.setattr(cache_mod, "open_entry", lambda *a, **k: calls.append(1) or real(*a, **k))
    c = IndexCache(provider, dim=DIM)
    with snap() as s:
        cold = c.entries(s, {streams["a"]: heads})[streams["a"]]
    with snap() as s:
        warm = c.entries(s, {streams["a"]: heads})[streams["a"]]
    assert set(cold) == set(heads) and {v: e.text for v, e in cold.items()} == {v: e.text for v, e in warm.items()}
    assert len(calls) == 2                                   # decrypted on the cold call only


def test_it_refuses_a_non_snapshot_session(two, rw, provider, streams):
    with rw() as s, pytest.raises(CacheRefused, match="snapshot"):
        IndexCache(provider, dim=DIM).entries(s, {streams["a"]: two[0]})


def test_another_principal_is_never_served_what_a_warm_cache_holds(two, snap, grant, provider, streams):
    heads, _ = two
    c = IndexCache(provider, dim=DIM)
    with snap() as s:
        c.entries(s, {streams["a"]: heads})                  # warm, loaded by the granted principal
    other = uuid.uuid4()
    grant(other, streams["b"])                               # granted elsewhere only
    with snap(other) as s, pytest.raises(CacheRefused, match="no read grant"):
        c.entries(s, {streams["a"]: heads})


def test_a_revoked_grant_serves_nothing_from_the_next_recall(two, snap, grant, provider, streams, principal):
    heads, _ = two
    c = IndexCache(provider, dim=DIM)
    with snap() as s:
        assert c.entries(s, {streams["a"]: heads})[streams["a"]]
    grant(principal, streams["a"], read=False)
    with snap() as s, pytest.raises(CacheRefused):
        c.entries(s, {streams["a"]: heads})


def test_erasure_is_honoured_by_the_next_recall_in_every_process(two, snap, provider, streams, migrated_db):
    heads, keys = two
    erased, kept = heads
    proc1, proc2 = IndexCache(provider, dim=DIM), IndexCache(provider, dim=DIM)   # two processes' caches
    for c in (proc1, proc2):
        with snap() as s:
            assert set(c.entries(s, {streams["a"]: heads})[streams["a"]]) == set(heads)
    _erase_key(migrated_db, streams["a"], keys[erased])
    for c in (proc1, proc2):
        with snap() as s:
            assert set(c.entries(s, {streams["a"]: heads})[streams["a"]]) == {kept}   # positive control kept


def test_a_snapshot_older_than_the_erasure_may_still_see_it_but_the_next_one_never(two, snap, provider, streams,
                                                                                   migrated_db):
    heads, keys = two
    c = IndexCache(provider, dim=DIM)
    with snap() as old:
        old.conn.execute("SELECT 1")                         # the old snapshot is taken here
        _erase_key(migrated_db, streams["a"], keys[heads[0]])
        assert heads[0] in c.entries(old, {streams["a"]: heads})[streams["a"]]   # began before the erasure
    with snap() as s:
        assert heads[0] not in c.entries(s, {streams["a"]: heads})[streams["a"]]


def test_a_generation_or_embedder_change_drops_the_stream(two, rw, snap, provider, streams):
    heads, _ = two
    c = IndexCache(provider, dim=DIM)
    with snap() as s:
        assert c.entries(s, {streams["a"]: heads})[streams["a"]]
    with rw() as s:
        s.conn.execute("INSERT INTO recall.index_generations (stream_id, generation, embedder_id) VALUES (%s, 2, %s)",
                       (streams["a"], "next-model@2#y"))
        s.conn.execute("INSERT INTO recall.index_switches (stream_id, generation, reason) VALUES (%s, 2, "
                       "'embedder_change')", (streams["a"],))
    with snap() as s:
        assert c.entries(s, {streams["a"]: heads})[streams["a"]] == {}   # generation 2 holds no entries yet


def test_the_memory_cap_evicts_least_recently_used_streams(two, snap, provider, streams):
    heads, _ = two
    c = IndexCache(provider, dim=DIM, max_bytes=1)
    with snap() as s:
        assert c.entries(s, {streams["a"]: heads})[streams["a"]]   # served, then evicted for the cap
    assert c.cached_streams() == []


def test_unknown_or_unindexed_versions_are_simply_absent(two, snap, provider, streams):
    with snap() as s:
        got = IndexCache(provider, dim=DIM).entries(s, {streams["a"]: [uuid.uuid4()]})
    assert got == {streams["a"]: {}}


# ---- the every-pair cross-scope suite through the cache (owner, D-0024 decision 2) ----
KINDS = ["org", "team", "project", "user", "agent"]


@pytest.fixture
def kinds(migrated_db):
    """Two registered streams of every kind; [kind][1] of 'org' is a second org."""
    org, org2 = uuid.uuid4(), uuid.uuid4()
    out = {k: ([org, org2] if k == "org" else [uuid.uuid4(), uuid.uuid4()]) for k in KINDS}
    with psycopg.connect(migrated_db["admin"]) as admin:
        for k, ids in out.items():
            for sid in ids:
                o = sid if k == "org" else org
                admin.execute("INSERT INTO scopes.scopes (stream_id, kind, org_id, source_event_id) VALUES (%s,%s,%s,%s)",
                              (sid, k, o, uuid.uuid4()))
    return out


@pytest.mark.parametrize("warm", [True, False], ids=["warm", "cold"])
@pytest.mark.parametrize("mine_kind,other_kind", [(a, b) for a in KINDS for b in KINDS], ids=lambda k: k)
def test_every_kind_pair_recall_through_the_cache_serves_only_granted_scopes(kinds, migrated_db, snap, provider,
                                                                             mine_kind, other_kind, warm):
    from contextlib import contextmanager

    from nacre.core.db import DbRole, connect
    from nacre.scopes.open_scoped_session import open_scoped_session
    mine, other = kinds[mine_kind][0], kinds[other_kind][1]
    org_of = lambda st: st if st in kinds["org"] else kinds["org"][0]   # noqa: E731
    seq = iter(range(5 * 10**6 + 1, 6 * 10**6))

    def grant(p, st, append):
        with psycopg.connect(migrated_db["admin"]) as admin:
            admin.execute("INSERT INTO scopes.scope_grants (principal_id, stream_id, org_id, can_read, can_append, "
                          "source_event_id, source_seq) VALUES (%s,%s,%s,true,%s,%s,%s)",
                          (p, st, org_of(st), append, uuid.uuid4(), next(seq)))

    @contextmanager
    def session(p):
        with connect(DbRole.APP, dsn=migrated_db["app"]) as conn, open_scoped_session(conn, p) as s:
            yield s

    writer, reader = uuid.uuid4(), uuid.uuid4()
    for stream, text in ((mine, "Mine: rotate the staging certificate weekly."),
                         (other, "Other: never deploy on Fridays without approval.")):
        grant(writer, stream, True)
        with session(writer) as s:
            belief(s, provider, stream, text, text.split(":")[0])
    with session(writer) as s:
        heads = {st: _heads(s, st) for st in (mine, other)}
    c = IndexCache(provider, dim=DIM)
    if warm:
        with snap(writer) as s:                               # another principal warms BOTH streams
            assert all(c.entries(s, {st: heads[st]})[st] for st in (mine, other))
    grant(reader, mine, False)
    with snap(reader) as s:
        got = c.entries(s, {mine: heads[mine]})[mine]
        assert {e.text for e in got.values()} == {"Mine"}
        with pytest.raises(CacheRefused):
            c.entries(s, {other: heads[other]})
        with pytest.raises(CacheRefused):
            c.entries(s, {mine: heads[mine], other: heads[other]})   # one ungranted stream refuses the whole call
