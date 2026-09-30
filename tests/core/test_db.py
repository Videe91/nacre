"""Tests for core/db.py against the real Docker Postgres (D-0006)."""
import uuid
from datetime import timedelta

import psycopg
import pytest

from nacre.core.db import DbRole, connect


def test_transactions_are_read_committed(pg_dsn):
    with connect(DbRole.MIGRATOR, dsn=pg_dsn) as conn:
        assert conn.execute("SHOW transaction_isolation").fetchone()[0] == "read committed"


def test_read_committed_wins_over_a_serializable_role_default(pg_dsn):
    # D-0003 visibility order breaks under REPEATABLE READ / SERIALIZABLE, so a default must not leak in.
    role = f"r_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(pg_dsn, autocommit=True) as admin:
        admin.execute(f"CREATE ROLE {role} LOGIN PASSWORD 'pw'")
        admin.execute(f"ALTER ROLE {role} SET default_transaction_isolation = 'serializable'")
    try:
        info = psycopg.conninfo.conninfo_to_dict(pg_dsn)
        info.update(user=role, password="pw")
        with connect(DbRole.MIGRATOR, dsn=psycopg.conninfo.make_conninfo(**info)) as conn:
            assert conn.execute("SHOW default_transaction_isolation").fetchone()[0] == "serializable"
            assert conn.execute("SHOW transaction_isolation").fetchone()[0] == "read committed"
    finally:
        with psycopg.connect(pg_dsn, autocommit=True) as admin:
            admin.execute(f"DROP ROLE {role}")


def test_session_time_zone_is_utc(pg_dsn):
    with connect(DbRole.MIGRATOR, dsn=pg_dsn) as conn:
        assert conn.execute("SHOW TimeZone").fetchone()[0] == "UTC"
        now = conn.execute("SELECT now()").fetchone()[0]
        assert now.utcoffset() == timedelta(0)


def test_nothing_is_committed_implicitly(pg_dsn):
    table = f"t_{uuid.uuid4().hex}"
    conn = connect(DbRole.MIGRATOR, dsn=pg_dsn)
    assert conn.autocommit is False
    conn.execute(f"CREATE TABLE {table} (x int)")
    conn.close()  # closing without commit must roll back
    with psycopg.connect(pg_dsn) as other:
        assert other.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0] is None


def test_application_name_names_the_role(pg_dsn):
    with connect(DbRole.VERIFIER, dsn=pg_dsn) as conn:
        assert conn.execute("SHOW application_name").fetchone()[0] == "nacre-verifier"


def test_dsn_comes_from_role_env_var(pg_dsn, monkeypatch):
    monkeypatch.setenv("NACRE_DSN_APP", pg_dsn)
    with connect(DbRole.APP) as conn:
        assert conn.execute("SELECT 1").fetchone()[0] == 1


def test_missing_dsn_names_the_env_var(monkeypatch):
    monkeypatch.delenv("NACRE_DSN_APP", raising=False)
    with pytest.raises(LookupError, match="NACRE_DSN_APP"):
        connect(DbRole.APP)
