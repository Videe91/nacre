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
