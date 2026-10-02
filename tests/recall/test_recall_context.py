"""Tests for recall/recall_context.py (R19), recall/record_context_assembled.py (R18) and recall/replay_frame.py (R20):
D-0025 amendment 2 (owner, 2026-10-02): the trace is one event in the issuing stream with no recalled content, the
query under the requester's key, per-item MACs under each item's own key, the frame hash; replay verifies before
erasure and reports the erased item as shredded after, while the other items still verify."""
import uuid

import psycopg
import pytest

from recall_kit import belief
from nacre.core.db import DbRole, connect
from nacre.core.event import ActorKind
from nacre.recall.embed_local import DIM
from nacre.recall.index_version import default_embedder
from nacre.recall.load_index_cache import IndexCache
from nacre.recall.recall_context import RecallRequest, recall_context
from nacre.recall.record_context_assembled import read_trace
from nacre.recall.replay_frame import replay_frame

P, Q = uuid.UUID(int=501), uuid.UUID(int=502)
TEXTS = [("Pin the payments base image by digest before release.", "Pin the payments base image by digest", P),
         ("Run the schema migration check before merging.", "Run the schema migration check", Q),
         ("Rotate the payments signing certificate weekly.", "Rotate the payments signing certificate", None)]


@pytest.fixture
def world(rw, provider, streams):
    with rw() as s:
        for text, nucleus, person in TEXTS:
            belief(s, provider, streams["a"], text, nucleus, person=person)
    return streams["a"]


def _recall(streams, principal, provider, cache, a, **kw):
    with connect(DbRole.APP, dsn=streams["dsn"]["app"]) as conn:
        return recall_context(conn, provider, principal, RecallRequest(a, (("project", a),),
                              "Should the payments release pin the base image?", **kw),
                              cache=cache, embedder=default_embedder(), tau_strong_q=6000, config_version="test-1")


def _replay(streams, principal, provider, cache, result):
    with connect(DbRole.APP, dsn=streams["dsn"]["app"]) as conn:
        return replay_frame(conn, provider, principal, result.trace_stream, result.trace_commit_seq, cache=cache,
                            embedder=default_embedder())


def _erase_key_of(migrated_db, rw, version_event_id, stream):
    with rw() as s:
        key = s.conn.execute("SELECT key_id FROM ledger.events WHERE event_id = %s", (version_event_id,)).fetchone()[0]
    with psycopg.connect(migrated_db["keyadmin"]) as ka:
        ka.execute("DELETE FROM keys.data_keys WHERE key_id = %s", (key,))
        ka.execute("INSERT INTO keys.shred_epochs (stream_id, epoch) VALUES (%s, 1) ON CONFLICT (stream_id) "
                   "DO UPDATE SET epoch = keys.shred_epochs.epoch + 1", (stream,))


def test_a_recall_commits_one_trace_with_no_recalled_content(world, rw, principal, provider, streams):
    cache = IndexCache(provider, dim=DIM)
    r = _recall(streams, principal, provider, cache, world)
    assert r.frame.body["items"] and r.trace_stream == world
    with rw() as s:
        trace = read_trace(s, provider, world, r.trace_commit_seq)
        raw = s.conn.execute("SELECT count(*) FROM ledger.events WHERE stream_id = %s AND commit_seq > %s",
                             (world, r.trace_commit_seq)).fetchone()[0]
    assert raw == 0 and trace.frame_id == r.frame.frame_id and trace.query.startswith("Should the payments")
    body_text = repr(trace)
    assert not any(t in body_text for t, _, _ in TEXTS) and not any(n in body_text for _, n, _ in TEXTS)
    assert len(trace.items) == len(r.frame.body["items"]) and all(len(i.mac) == 32 for i in trace.items)


def test_the_query_is_under_the_requesters_key_and_on_behalf_of_uses_the_persons(world, rw, principal, provider,
                                                                                  streams):
    cache = IndexCache(provider, dim=DIM)
    person = uuid.UUID(int=777)
    plain = _recall(streams, principal, provider, cache, world)
    delegated = _recall(streams, principal, provider, cache, world, on_behalf_of=person)
    with rw() as s:
        subj = {seq: s.conn.execute("SELECT k.subject_id FROM ledger.events e JOIN keys.data_keys k ON k.key_id = e.key_id "
                                    "WHERE e.stream_id = %s AND e.commit_seq = %s", (world, seq)).fetchone()[0]
                for seq in (plain.trace_commit_seq, delegated.trace_commit_seq)}
    assert subj[plain.trace_commit_seq] == world and subj[delegated.trace_commit_seq] == person


def test_replay_verifies_every_item_before_erasure_and_the_frame_hash_matches(world, principal, provider, streams):
    cache = IndexCache(provider, dim=DIM)
    r = _recall(streams, principal, provider, cache, world)
    rep = _replay(streams, principal, provider, IndexCache(provider, dim=DIM), r)
    assert rep.status == "replayed" and set(rep.items.values()) == {"verified"} and rep.frame_match is True


def test_after_erasure_the_item_replays_shredded_and_the_others_still_verify(world, rw, principal, provider, streams,
                                                                            migrated_db):
    cache = IndexCache(provider, dim=DIM)
    r = _recall(streams, principal, provider, cache, world)
    items = [uuid.UUID(i["version_event_id"]) for i in r.frame.body["items"]]
    with rw() as s:
        by_text = {i["text"]: uuid.UUID(i["version_event_id"]) for i in r.frame.body["items"]}
    erased = by_text["Pin the payments base image by digest"]                  # authored by person P only
    _erase_key_of(migrated_db, rw, erased, world)
    rep = _replay(streams, principal, provider, IndexCache(provider, dim=DIM), r)
    assert rep.items[erased] == "shredded"
    assert all(v == "verified" for k, v in rep.items.items() if k != erased) and len(rep.items) == len(items)
    assert rep.frame_match is None                                          # not comparable, never "False"


def test_an_unreadable_trace_replays_as_such(world, rw, principal, provider, streams, migrated_db):
    cache = IndexCache(provider, dim=DIM)
    person = uuid.UUID(int=778)
    r = _recall(streams, principal, provider, cache, world, on_behalf_of=person)
    with rw() as s:
        key = s.conn.execute("SELECT key_id FROM ledger.events WHERE event_id = %s", (r.trace_event_id,)).fetchone()[0]
    with psycopg.connect(migrated_db["keyadmin"]) as ka:
        ka.execute("DELETE FROM keys.data_keys WHERE key_id = %s", (key,))
    assert _replay(streams, principal, provider, cache, r).status == "trace_unreadable"


def test_no_frame_leaves_without_a_committed_trace(world, grant, provider, streams):
    reader = uuid.uuid4()
    grant(reader, world, read=True, append=False)                          # may read, may not write the trace
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _recall(streams, reader, provider, IndexCache(provider, dim=DIM), world)


# ---- the every-pair cross-scope suite end to end through recall_context (owner, D-0024 decision 2) ----
KINDS = ["org", "team", "project", "user", "agent"]


@pytest.mark.parametrize("mine_kind,other_kind", [(a, b) for a in KINDS for b in KINDS], ids=lambda k: k)
def test_every_kind_pair_a_recall_returns_only_granted_scopes(migrated_db, provider, mine_kind, other_kind):
    from contextlib import contextmanager
    from nacre.recall.freeze_snapshot import SnapshotRefused
    from nacre.scopes.open_scoped_session import open_scoped_session
    org, org2 = uuid.uuid4(), uuid.uuid4()
    kinds = {k: ([org, org2] if k == "org" else [uuid.uuid4(), uuid.uuid4()]) for k in KINDS}
    seq = iter(range(7 * 10**6 + 1, 8 * 10**6))
    with psycopg.connect(migrated_db["admin"]) as admin:
        for k, ids in kinds.items():
            for sid in ids:
                admin.execute("INSERT INTO scopes.scopes (stream_id, kind, org_id, source_event_id) VALUES (%s,%s,%s,%s)",
                              (sid, k, sid if k == "org" else org, uuid.uuid4()))
    mine, other = kinds[mine_kind][0], kinds[other_kind][1]
    org_of = lambda st: st if st in kinds["org"] else org          # noqa: E731

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
    for stream, text in ((mine, "Mine: rotate the staging certificate weekly 2026."),
                         (other, "Other: never deploy on Fridays without approval 2026.")):
        grant(writer, stream, True)
        with session(writer) as s:
            belief(s, provider, stream, text, text.split(":")[0])
    grant(reader, mine, True)
    cache = IndexCache(provider, dim=DIM)
    with connect(DbRole.APP, dsn=migrated_db["app"]) as conn:
        r = recall_context(conn, provider, reader, RecallRequest(mine, (("task", mine),), "certificate rotation"),
                           cache=cache, embedder=default_embedder(), tau_strong_q=6000, config_version="test-1")
    assert {i["text"] for i in r.frame.body["items"]} == {"Mine"}
    with connect(DbRole.APP, dsn=migrated_db["app"]) as conn, pytest.raises(SnapshotRefused):
        recall_context(conn, provider, reader, RecallRequest(mine, (("task", mine), ("team", other)), "deploy"),
                       cache=cache, embedder=default_embedder(), tau_strong_q=6000, config_version="test-1")
