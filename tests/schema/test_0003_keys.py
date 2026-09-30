"""Tests for schema/sql/0003_keys.sql: keys behind forced RLS, the nonce cap, cascade on scope deletion."""
import uuid
from datetime import date

import psycopg
import pytest


@pytest.mark.parametrize("table", ["keys.stream_master_keys", "keys.data_keys"])
def test_s2_rls_enabled_and_forced(migrated_db, table):
    with psycopg.connect(migrated_db["admin"]) as conn:
        flags = conn.execute("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = %s::regclass",
                             (table,)).fetchone()
    assert flags == (True, True)


def _add_keys(conn, stream_id, month=date(2026, 9, 1)):
    conn.execute("INSERT INTO keys.stream_master_keys (stream_id, root_key_version, wrapped_key) VALUES (%s, 'v1', 'w')",
                 (stream_id,))
    key_id = uuid.uuid4()
    conn.execute("""INSERT INTO keys.data_keys (key_id, stream_id, subject_id, month, wrapped_key)
                    VALUES (%s, %s, %s, %s, 'w')""", (key_id, stream_id, stream_id, month))
    return key_id


@pytest.fixture
def keyed_streams(migrated_db):
    a, b = uuid.uuid4(), uuid.uuid4()
    with psycopg.connect(migrated_db["admin"]) as conn:
        _add_keys(conn, a)
        _add_keys(conn, b)
    return a, b


def test_s3_app_sees_only_keys_of_readable_streams(migrated_db, helpers, keyed_streams):
    a, _ = keyed_streams
    with psycopg.connect(migrated_db["app"]) as conn:
        helpers.scope_to(conn, read=[a])
        assert {r[0] for r in conn.execute("SELECT stream_id FROM keys.stream_master_keys")} == {a}
        assert {r[0] for r in conn.execute("SELECT stream_id FROM keys.data_keys")} == {a}


def test_s3_no_setting_means_no_keys(migrated_db, keyed_streams):
    with psycopg.connect(migrated_db["app"]) as conn:
        assert conn.execute("SELECT count(*) FROM keys.data_keys").fetchone()[0] == 0


def test_app_can_count_encryptions_but_not_rewrite_or_delete_keys(migrated_db, helpers, keyed_streams):
    a, _ = keyed_streams
    with psycopg.connect(migrated_db["app"]) as conn:
        helpers.scope_to(conn, read=[a], write=[a])
        assert conn.execute("UPDATE keys.data_keys SET encryption_count = encryption_count + 1").rowcount == 1
        for stmt in ("UPDATE keys.data_keys SET wrapped_key = 'x'", "DELETE FROM keys.data_keys",
                     "DELETE FROM keys.stream_master_keys"):
            conn.execute("SAVEPOINT sp")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(stmt)
            conn.execute("ROLLBACK TO SAVEPOINT sp")


def test_encryption_count_is_capped_at_2_to_the_28(migrated_db, keyed_streams):
    with psycopg.connect(migrated_db["admin"]) as conn:
        conn.execute("UPDATE keys.data_keys SET encryption_count = 268435456")
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("UPDATE keys.data_keys SET encryption_count = 268435457")


def test_month_must_be_the_first_of_a_month(migrated_db):
    s = uuid.uuid4()
    with psycopg.connect(migrated_db["admin"]) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            _add_keys(conn, s, month=date(2026, 9, 15))


def test_one_data_key_per_stream_subject_month(migrated_db):
    s = uuid.uuid4()
    with psycopg.connect(migrated_db["admin"]) as conn:
        _add_keys(conn, s)
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute("""INSERT INTO keys.data_keys (key_id, stream_id, subject_id, month, wrapped_key)
                            VALUES (%s, %s, %s, '2026-09-01', 'w')""", (uuid.uuid4(), s, s))


def test_destroying_a_stream_master_key_destroys_its_data_keys(migrated_db, keyed_streams):
    a, b = keyed_streams
    with psycopg.connect(migrated_db["admin"]) as conn:
        conn.execute("DELETE FROM keys.stream_master_keys WHERE stream_id = %s", (a,))
        left = {r[0] for r in conn.execute("SELECT stream_id FROM keys.data_keys")}
    assert left == {b}
