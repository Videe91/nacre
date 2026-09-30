"""Tests for scopes/open_scoped_session.py: the adversarial cross-scope suite (D-0005 S-3, gate item 5)."""
import itertools
import uuid

import psycopg
import pytest

from nacre.core.db import DbRole, connect
from nacre.scopes.open_scoped_session import ScopeError, open_scoped_session

KINDS = ["org", "team", "project", "user", "agent"]  # as in tests/scopes/conftest.py

DENIED = psycopg.errors.InsufficientPrivilege


def _app(world):
    return connect(DbRole.APP, dsn=world["dsn"]["app"])


def _seen(conn, table="ledger.events"):
    return {r[0] for r in conn.execute(f"SELECT DISTINCT stream_id FROM {table}")}


# --- gate item 5: every scope-kind pair, both directions -----------------------------------

@pytest.mark.parametrize("granted_kind,other_kind", list(itertools.product(KINDS, KINDS)), ids=lambda k: k)
def test_s3_cross_scope_read_and_append_fail_for_every_kind_pair(world, grant, granted_kind, other_kind):
    mine = world["streams"][granted_kind][0]
    other = world["streams"][other_kind][1]        # index 1: never the same stream, even for same kind
    p = uuid.uuid4()
    grant(p, mine, read=True, append=True)
    with _app(world) as conn, open_scoped_session(conn, p) as s:
        assert _seen(s.conn) == {mine}
        assert s.conn.execute("SELECT count(*) FROM ledger.events WHERE stream_id = %s", (other,)).fetchone()[0] == 0
        assert _seen(s.conn, "keys.data_keys") == {mine}
        assert _seen(s.conn, "keys.stream_master_keys") == {mine}
        assert other not in _seen(s.conn, "scopes.scopes")
        with pytest.raises(DENIED, match="row-level security"):
            s.conn.execute("INSERT INTO keys.stream_master_keys (stream_id, root_key_version, wrapped_key) "
                           "VALUES (%s, 'v1', 'w')", (other,))


def test_s3_principal_with_no_grants_sees_nothing(world):
    with _app(world) as conn, open_scoped_session(conn, uuid.uuid4()) as s:
        assert _seen(s.conn) == set()
        assert _seen(s.conn, "keys.data_keys") == set()


def test_s3_read_only_grant_cannot_append(world, grant):
    p, proj = uuid.uuid4(), world["streams"]["project"][0]
    grant(p, proj, read=True, append=False)
    with _app(world) as conn, open_scoped_session(conn, p) as s:
        assert _seen(s.conn) == {proj}
        with pytest.raises(DENIED, match="row-level security"):
            s.conn.execute("INSERT INTO keys.data_keys (key_id, stream_id, subject_id, month, wrapped_key) "
                           "VALUES (%s, %s, %s, '2026-10-01', 'w')", (uuid.uuid4(), proj, proj))


def test_s3_reused_connection_carries_nothing_between_principals(world, grant):
    p, q = uuid.uuid4(), uuid.uuid4()
    a, b = world["streams"]["project"]
    grant(p, a)
    grant(q, b)
    with _app(world) as conn:
        with open_scoped_session(conn, p) as s:
            assert _seen(s.conn) == {a}
        assert _seen(conn) == set()          # raw query after the session: nothing
        conn.rollback()
        with open_scoped_session(conn, q) as s:
            assert _seen(s.conn) == {b}      # q never sees p's stream on the same connection
        assert _seen(conn) == set()


def test_s3_settings_die_on_error_rollback(world, grant):
    p, proj = uuid.uuid4(), world["streams"]["project"][0]
    grant(p, proj)
    with _app(world) as conn:
        with pytest.raises(ZeroDivisionError):
            with open_scoped_session(conn, p):
                1 / 0
        assert _seen(conn) == set()


def test_s3_revoke_takes_effect_on_the_next_session(world, grant):
    p, proj = uuid.uuid4(), world["streams"]["project"][0]
    grant(p, proj)
    with _app(world) as conn:
        with open_scoped_session(conn, p) as s:
            assert _seen(s.conn) == {proj}
        grant(p, proj, read=False, append=False)
        with open_scoped_session(conn, p) as s:
            assert _seen(s.conn) == set()


# --- pre-flight refusals --------------------------------------------------------------------

def test_refuses_inside_an_open_transaction(world):
    with _app(world) as conn:
        conn.execute("SELECT 1")                       # implicit transaction now open
        with pytest.raises(ScopeError, match="already inside a transaction"):
            with open_scoped_session(conn, uuid.uuid4()):
                pass


def test_refuses_nesting(world):
    with _app(world) as conn, open_scoped_session(conn, uuid.uuid4()):
        with pytest.raises(ScopeError, match="already inside a transaction"):
            with open_scoped_session(conn, uuid.uuid4()):
                pass


def test_refuses_a_superuser_connection(world):
    with connect(DbRole.MIGRATOR, dsn=world["dsn"]["admin"]) as conn:
        with pytest.raises(ScopeError, match="bypasses row-level security"):
            with open_scoped_session(conn, uuid.uuid4()):
                pass


def test_refuses_other_isolation_levels(world):
    with _app(world) as conn:
        conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        with pytest.raises(ScopeError, match="READ COMMITTED"):
            with open_scoped_session(conn, uuid.uuid4()):
                pass


def test_session_commits_on_success(world, grant):
    p, proj = uuid.uuid4(), world["streams"]["project"][0]
    grant(p, proj, read=True, append=True)
    with _app(world) as conn:
        with open_scoped_session(conn, p) as s:
            s.conn.execute("UPDATE keys.data_keys SET encryption_count = encryption_count + 1 WHERE stream_id = %s", (proj,))
    with psycopg.connect(world["dsn"]["admin"]) as admin:
        assert admin.execute("SELECT encryption_count FROM keys.data_keys WHERE stream_id = %s", (proj,)).fetchone()[0] == 1


def test_a_pooled_connection_never_carries_scope_between_principals(world, grant):
    # D-0006 amendment 1 condition: pool size 1 forces the SAME connection to serve every principal in turn.
    from nacre.core.db import open_pool
    from nacre.scopes.open_scoped_session import open_scoped_session
    p1, p2 = uuid.uuid4(), uuid.uuid4()
    a, b = world["streams"]["project"]
    grant(p1, a)
    grant(p2, b)
    seen = lambda c: {r[0] for r in c.execute("SELECT DISTINCT stream_id FROM ledger.events")}  # noqa: E731
    with open_pool(DbRole.APP, dsn=world["dsn"]["app"], min_size=1, max_size=1) as pool:
        with pool.connection() as conn:
            backend = conn.info.backend_pid
            with open_scoped_session(conn, p1) as s:
                assert seen(s.conn) == {a}
        with pool.connection() as conn:
            assert conn.info.backend_pid == backend                         # really the same connection
            assert seen(conn) == set()                                      # nothing left over outside a session
            conn.rollback()
            with open_scoped_session(conn, p2) as s:
                assert seen(s.conn) == {b}                                  # never p1's stream
        with pool.connection() as conn:                                     # a borrower that errors mid-session
            try:
                with open_scoped_session(conn, p1):
                    raise RuntimeError("boom")
            except RuntimeError:
                pass
        with pool.connection() as conn:
            assert conn.info.backend_pid == backend and seen(conn) == set()


# --- gate item 5, re-run at the gate with REAL appended events (encrypted bodies + attachments) -------------------

def test_gate5_real_events_do_not_cross_scopes(session, streams, provider, tmp_path):
    from nacre.core.event import ActorKind, EventType, PayloadType, Source
    from nacre.ledger.append_event import AppendRequest, append_event
    from nacre.ledger.local_disk_blob_store import LocalDiskBlobStore
    from nacre.ledger.read_attachment import AttachmentReadError, read_attachment
    from nacre.ledger.read_stream import ReadError, read_stream

    blobs = LocalDiskBlobStore(tmp_path / "blobs")
    a, b, p, q = streams["a"], streams["b"], uuid.uuid4(), uuid.uuid4()

    def req(stream, text):
        return AppendRequest(stream_id=stream, event_type=EventType.RESULT, payload_type=PayloadType.TEXT,
                             actor_kind=ActorKind.TOOL, actor_id=uuid.UUID(int=7), source=Source.TOOL,
                             idempotency_key=str(uuid.uuid4()), content=text, attachment=text.encode() * 3,
                             attachment_media_type="text/plain", attachment_description="log")
    with session(p, read=[a], write=[a]) as s:
        env_a = append_event(s, provider, req(a, "a's secret plan"), blob_store=blobs).envelope
    with session(q, read=[b], write=[b]) as s:
        env_b = append_event(s, provider, req(b, "b's secret plan"), blob_store=blobs).envelope

    with session(p, read=[a], write=[a]) as s:
        assert [e.envelope.event_id for e in read_stream(s, provider, a)] == [env_a.event_id]
        assert read_attachment(s, provider, blobs, env_a) == b"a's secret plan" * 3
        with pytest.raises(ReadError, match="not readable"):
            read_stream(s, provider, b)
        with pytest.raises(AttachmentReadError, match="not readable"):
            read_attachment(s, provider, blobs, env_b)                   # even holding b's envelope and the blob
        assert _seen(s.conn) == {a}
        assert _seen(s.conn, "keys.data_keys") == {a} and _seen(s.conn, "keys.stream_master_keys") == {a}
    with pytest.raises(DENIED, match="row-level security"):              # refused by the database, not by trust
        with session(p, read=[a], write=[a]) as s:
            append_event(s, provider, req(b, "p writes into b"), blob_store=blobs)
    with session(q, read=[b], write=[b]) as s:
        assert [e.envelope.event_id for e in read_stream(s, provider, b)] == [env_b.event_id]   # nothing from p
