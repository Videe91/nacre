"""Test infrastructure: a real Postgres (D-0006). DB tests FAIL, never skip, when it is down."""
import os
import uuid

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from nacre.core.db import DbRole, connect
from nacre.schema.apply_migrations import apply_migrations

DEFAULT_TEST_DSN = "postgresql://postgres:nacre_dev@127.0.0.1:54329/postgres"
TEST_ROLE_PASSWORD = "nacre_test_only"


def dsn_with(dsn: str, **changes: str) -> str:
    info = conninfo_to_dict(dsn)
    info.update(changes)
    return make_conninfo(**info)


@pytest.fixture(scope="session")
def pg_dsn() -> str:
    """Superuser DSN of the Docker test Postgres. Override with NACRE_TEST_DSN."""
    dsn = os.environ.get("NACRE_TEST_DSN", DEFAULT_TEST_DSN)
    try:
        psycopg.connect(dsn, connect_timeout=3).close()
    except psycopg.OperationalError as exc:
        pytest.fail(f"Test Postgres unreachable at {dsn!r}: run `docker compose up -d --wait`. ({exc})")
    return dsn


@pytest.fixture
def fresh_db(pg_dsn):
    """An empty database for one test, dropped afterwards. Yields its superuser DSN."""
    name = f"nacre_t_{uuid.uuid4().hex[:16]}"
    with psycopg.connect(pg_dsn, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    yield dsn_with(pg_dsn, dbname=name)
    with psycopg.connect(pg_dsn, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')


@pytest.fixture
def migrated_db(fresh_db):
    """A fresh database with all migrations applied. Returns DSNs keyed 'admin', 'app', 'verifier'.
    Test-only: enables LOGIN on the cluster-wide NOLOGIN roles with a test password."""
    with connect(DbRole.MIGRATOR, dsn=fresh_db) as conn:
        apply_migrations(conn)
    with psycopg.connect(fresh_db, autocommit=True) as admin:
        for role in ("nacre_app", "nacre_verifier"):
            admin.execute(f"ALTER ROLE {role} LOGIN PASSWORD '{TEST_ROLE_PASSWORD}'")
    return {
        "admin": fresh_db,
        "app": dsn_with(fresh_db, user="nacre_app", password=TEST_ROLE_PASSWORD),
        "verifier": dsn_with(fresh_db, user="nacre_verifier", password=TEST_ROLE_PASSWORD),
    }
