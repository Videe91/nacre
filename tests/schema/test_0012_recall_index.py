"""Tests for migration 0012: the encrypted recall index (D-0024): ciphertext-only columns, entries backed by a memory
event under the same key, one embedder per generation, forward-only switches, append-only, scoped; and the per-stream
shred epoch, which only keyadmin may write."""
import uuid

import psycopg
import pytest

from nacre.core.event import ActorKind, EventType, PayloadType, Source
from nacre.ledger.append_event import AppendRequest, Authorship, append_event

EMB = "minilm-l6-v2@1110a243#sha256-0000"


def _event(s, provider, stream, etype=EventType.MEMORY_EVENT):
    return append_event(s, provider, AppendRequest(
        stream_id=stream, event_type=etype, payload_type=PayloadType.STRUCTURED, actor_kind=ActorKind.SYSTEM,
        actor_id=uuid.UUID(int=1), source=Source.SYSTEM, authorship=Authorship.SCOPE_PRINCIPAL,
        idempotency_key=str(uuid.uuid4()), content={"op": "version"})).envelope


def _generation(s, stream, generation=1, embedder=EMB):
    s.conn.execute("INSERT INTO recall.index_generations (stream_id, generation, embedder_id) VALUES (%s, %s, %s)",
                   (stream, generation, embedder))


def _entry(s, env, generation=1, embedder=EMB, key_id=None, stream=None):
    s.conn.execute("INSERT INTO recall.index_entries (stream_id, index_generation, version_event_id, key_id, "
                   "embedder_id, body) VALUES (%s, %s, %s, %s, %s, %s)",
                   (stream or env.stream_id, generation, env.event_id, key_id or env.key_id, embedder, b"\x00" * 64))


@pytest.fixture
def rw(session, streams):
    p = uuid.uuid4()
    return lambda: session(p, read=[streams["a"], streams["b"]], write=[streams["a"]])


def test_only_structural_columns_and_ciphertext_exist(rw):
    with rw() as s:
        cols = {r[0] for r in s.conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = 'recall'")}
    assert cols == {"stream_id", "generation", "embedder_id", "created_at", "switched_at", "reason",
                    "index_generation", "version_event_id", "key_id", "seq", "body"}


def test_an_entry_backed_by_its_memory_event_under_the_same_key_is_accepted(rw, provider, streams):
    with rw() as s:
        _generation(s, streams["a"])
        env = _event(s, provider, streams["a"])
        _entry(s, env)
        assert s.conn.execute("SELECT count(*) FROM recall.index_entries").fetchone()[0] == 1


@pytest.mark.parametrize("bad", ["other_key", "not_memory_event", "embedder"])
def test_entries_that_do_not_match_their_event_or_generation_are_rejected(rw, provider, streams, bad):
    with rw() as s:
        _generation(s, streams["a"])
        env = _event(s, provider, streams["a"], EventType.STATEMENT if bad == "not_memory_event" else EventType.MEMORY_EVENT)
        kw = {"other_key": {"key_id": uuid.uuid4()}, "embedder": {"embedder": "other@1#x"}}.get(bad, {})
        with pytest.raises(psycopg.errors.RaiseException):
            _entry(s, env, **kw)


def test_entries_are_append_only(rw, provider, streams):
    with rw() as s:
        _generation(s, streams["a"])
        _entry(s, _event(s, provider, streams["a"]))
    for sql in ("UPDATE recall.index_entries SET body = '\\x01'", "DELETE FROM recall.index_entries"):
        with rw() as s, pytest.raises(psycopg.errors.InsufficientPrivilege):
            s.conn.execute(sql)


def test_append_only_holds_even_for_the_owner(migrated_db, rw, provider, streams):
    with rw() as s:
        _generation(s, streams["a"])
        _entry(s, _event(s, provider, streams["a"]))
    with psycopg.connect(migrated_db["admin"]) as admin:
        admin.execute("SET ROLE nacre_migrator")
        # FORCE RLS hides rows from the owner's DELETE; TRUNCATE reaches the append-only trigger.
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="append-only"):
            admin.execute("TRUNCATE recall.index_entries CASCADE")


def test_switches_move_forward_by_one_to_an_existing_generation(rw, streams):
    with rw() as s:
        _generation(s, streams["a"])
        _generation(s, streams["a"], 2, embedder="next-model@2#y")
        _generation(s, streams["a"], 3, embedder="next-model@2#y")
    with rw() as s, pytest.raises(psycopg.errors.RaiseException):   # skips generation 2
        s.conn.execute("INSERT INTO recall.index_switches (stream_id, generation, reason) VALUES (%s, 3, 'rebuild')",
                       (streams["a"],))
    with rw() as s:
        s.conn.execute("INSERT INTO recall.index_switches (stream_id, generation, reason) VALUES (%s, 2, 'embedder_change')",
                       (streams["a"],))
        assert s.conn.execute("SELECT recall.active_generation(%s)", (streams["a"],)).fetchone()[0] == 2


def test_rls_hides_unreadable_streams_and_refuses_unwritable_ones(session, rw, provider, streams):
    with rw() as s:
        _generation(s, streams["a"])
        _entry(s, _event(s, provider, streams["a"]))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _generation(s, streams["b"])                      # readable, not writable
    with session(uuid.uuid4(), read=[streams["b"]]) as s:
        assert s.conn.execute("SELECT count(*) FROM recall.index_entries").fetchone()[0] == 0
        assert s.conn.execute("SELECT count(*) FROM recall.index_generations").fetchone()[0] == 0


def test_shred_epochs_are_readable_in_scope_and_writable_only_by_keyadmin(migrated_db, session, streams):
    with psycopg.connect(migrated_db["keyadmin"]) as ka:
        ka.execute("INSERT INTO keys.shred_epochs (stream_id, epoch) VALUES (%s, 1), (%s, 4)", (streams["a"], streams["b"]))
    with session(uuid.uuid4(), read=[streams["a"]], write=[streams["a"]]) as s:
        assert s.conn.execute("SELECT stream_id, epoch FROM keys.shred_epochs").fetchall() == [(streams["a"], 1)]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            s.conn.execute("UPDATE keys.shred_epochs SET epoch = 9")
