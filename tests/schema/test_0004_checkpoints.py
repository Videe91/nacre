"""Tests for schema/sql/0004_checkpoints.sql: D-0005 amendment 2, invariants C-1..C-6."""
import uuid
from datetime import UTC, datetime

import psycopg
import pytest

DENIED = psycopg.errors.InsufficientPrivilege


def _checkpoint_row(stream_id, seq=1):
    return (uuid.uuid4(), stream_id, seq, b"\x07" * 32, datetime.now(UTC), "ckpt-key-1", b"\x09" * 64)


INSERT_CHECKPOINT = """INSERT INTO ledger.checkpoints
    (checkpoint_id, stream_id, commit_seq, head_hash, signed_at, signing_key_id, signature)
    VALUES (%s, %s, %s, %s, %s, %s, %s)"""


@pytest.fixture
def two_streams(migrated_db, helpers):
    a, b = uuid.uuid4(), uuid.uuid4()
    with psycopg.connect(migrated_db["admin"]) as conn:
        helpers.insert_chain(conn, a, 3)
        helpers.insert_chain(conn, b, 2)
        conn.execute(INSERT_CHECKPOINT, _checkpoint_row(a, 3))
    return a, b


def _denied(conn, sql, params=()):
    conn.execute("SAVEPOINT sp")
    with pytest.raises(DENIED):
        conn.execute(sql, params)
    conn.execute("ROLLBACK TO SAVEPOINT sp")


# C-1 ------------------------------------------------------------------------------------------

def test_c1_checkpointer_reads_heads_of_every_stream(migrated_db, two_streams):
    a, b = two_streams
    with psycopg.connect(migrated_db["checkpointer"]) as conn:
        heads = dict(conn.execute(
            "SELECT DISTINCT ON (stream_id) stream_id, commit_seq FROM ledger.events ORDER BY stream_id, commit_seq DESC"))
        assert heads == {a: 3, b: 2}
        assert all(len(bytes(h)) == 32 for (h,) in conn.execute("SELECT hash FROM ledger.events"))


@pytest.mark.parametrize("column", ["body_ciphertext", "event_id", "prev_hash", "key_id", "request_mac",
                                    "actor_id", "recorded_at", "*"])
def test_c1_checkpointer_cannot_read_any_other_column(migrated_db, two_streams, column):
    with psycopg.connect(migrated_db["checkpointer"]) as conn:
        with pytest.raises(DENIED):
            conn.execute(f"SELECT {column} FROM ledger.events")


# C-2 ------------------------------------------------------------------------------------------

def test_c2_checkpointer_records_and_reads_checkpoints_only(migrated_db, two_streams):
    a, b = two_streams
    with psycopg.connect(migrated_db["checkpointer"]) as conn:
        conn.execute(INSERT_CHECKPOINT, _checkpoint_row(b, 2))
        assert conn.execute("SELECT count(*) FROM ledger.checkpoints").fetchone()[0] == 2
        for sql, params in [("UPDATE ledger.checkpoints SET signature = %s", (b"\x00" * 64,)),
                            ("DELETE FROM ledger.checkpoints", ()),
                            ("TRUNCATE ledger.checkpoints", ()),
                            ("INSERT INTO ledger.events (event_id) VALUES (%s)", (uuid.uuid4(),))]:
            _denied(conn, sql, params)


# C-3 ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("stmt", ["UPDATE ledger.checkpoints SET commit_seq = 99",
                                  "DELETE FROM ledger.checkpoints", "TRUNCATE ledger.checkpoints"])
def test_c3_even_a_superuser_cannot_mutate_checkpoints(migrated_db, two_streams, stmt):
    with psycopg.connect(migrated_db["admin"]) as conn:
        with pytest.raises(DENIED, match="append-only"):
            conn.execute(stmt)


def test_c3_checkpoints_rls_is_forced(migrated_db):
    with psycopg.connect(migrated_db["admin"]) as conn:
        flags = conn.execute("SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                             "WHERE oid = 'ledger.checkpoints'::regclass").fetchone()
    assert flags == (True, True)


# C-4 ------------------------------------------------------------------------------------------

def test_c4_checkpoints_hold_no_key_material(migrated_db):
    with psycopg.connect(migrated_db["admin"]) as conn:
        columns = [r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'ledger' AND table_name = 'checkpoints' ORDER BY ordinal_position")]
    assert columns == ["checkpoint_id", "stream_id", "commit_seq", "head_hash", "signed_at",
                       "signing_key_id", "signature"]


# C-5 ------------------------------------------------------------------------------------------

def test_c5_verifier_reads_checkpoints_and_events_but_writes_nothing(migrated_db, two_streams):
    a, _ = two_streams
    with psycopg.connect(migrated_db["verifier"]) as conn:
        assert conn.execute("SELECT count(*) FROM ledger.checkpoints").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM ledger.events").fetchone()[0] == 5
        for sql, params in [(INSERT_CHECKPOINT, _checkpoint_row(a, 3)),
                            ("UPDATE ledger.checkpoints SET commit_seq = 1", ()),
                            ("DELETE FROM ledger.checkpoints", ()),
                            ("UPDATE ledger.events SET commit_seq = 1", ()),
                            ("DELETE FROM ledger.events", ()),
                            ("INSERT INTO ledger.events (event_id) VALUES (%s)", (uuid.uuid4(),))]:
            _denied(conn, sql, params)


# C-6 ------------------------------------------------------------------------------------------

def test_c6_checkpointer_owns_nothing_and_cannot_bypass_rls(migrated_db):
    with psycopg.connect(migrated_db["admin"]) as conn:
        flags = conn.execute("SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb FROM pg_roles "
                             "WHERE rolname = 'nacre_checkpointer'").fetchone()
        owned = conn.execute("""
            SELECT count(*) FROM (
              SELECT relowner AS o FROM pg_class UNION ALL SELECT proowner FROM pg_proc UNION ALL
              SELECT nspowner FROM pg_namespace UNION ALL SELECT typowner FROM pg_type) x
            WHERE o = (SELECT oid FROM pg_roles WHERE rolname = 'nacre_checkpointer')""").fetchone()[0]
    assert flags == (False, False, False, False)
    assert owned == 0


@pytest.mark.parametrize("table", ["scopes.scopes", "scopes.scope_grants", "keys.stream_master_keys", "keys.data_keys"])
def test_c6_checkpointer_has_no_access_to_scopes_or_keys(migrated_db, table):
    with psycopg.connect(migrated_db["checkpointer"]) as conn:
        with pytest.raises(DENIED):
            conn.execute(f"SELECT 1 FROM {table}")


def test_app_has_no_access_to_checkpoints(migrated_db):
    with psycopg.connect(migrated_db["app"]) as conn:
        with pytest.raises(DENIED):
            conn.execute("SELECT 1 FROM ledger.checkpoints")


# Grants and RLS each block writes; test the grant layer directly so neither can regress unseen.
@pytest.mark.parametrize("role,table,privilege,expected", [
    ("nacre_verifier", "ledger.checkpoints", "SELECT", True),
    *[("nacre_verifier", t, p, False) for t in ("ledger.checkpoints", "ledger.events")
      for p in ("INSERT", "UPDATE", "DELETE", "TRUNCATE")],
    ("nacre_checkpointer", "ledger.checkpoints", "SELECT", True),
    ("nacre_checkpointer", "ledger.checkpoints", "INSERT", True),
    *[("nacre_checkpointer", "ledger.checkpoints", p, False) for p in ("UPDATE", "DELETE", "TRUNCATE")],
    *[("nacre_checkpointer", "ledger.events", p, False) for p in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")],
], ids=lambda v: str(v))
def test_table_privileges(migrated_db, role, table, privilege, expected):
    # Table-level SELECT for the checkpointer is False: it holds column grants only (C-1).
    with psycopg.connect(migrated_db["admin"]) as conn:
        assert conn.execute("SELECT has_table_privilege(%s, %s, %s)", (role, table, privilege)).fetchone()[0] is expected
