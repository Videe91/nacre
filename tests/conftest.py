"""Test infrastructure: a real Postgres (D-0006). DB tests FAIL, never skip, when it is down."""
import os
import uuid
from contextlib import contextmanager
from itertools import count

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from nacre.core.db import DbRole, connect
from nacre.keys.local_file_root_key import LocalFileRootKeyProvider
from nacre.schema.apply_migrations import apply_migrations
from nacre.scopes.open_scoped_session import open_scoped_session

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
        # A lock wait in a test is a bug; make it an error instead of a hung run.
        admin.execute(f"ALTER DATABASE \"{name}\" SET lock_timeout = '10s'")
    yield dsn_with(pg_dsn, dbname=name)
    with psycopg.connect(pg_dsn, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')


@pytest.fixture
def migrated_db(fresh_db):
    """A fresh database with all migrations applied. DSNs keyed 'admin', 'app', 'verifier', 'checkpointer', 'keyadmin', 'gc'.
    Test-only: enables LOGIN on the cluster-wide NOLOGIN roles with a test password."""
    with connect(DbRole.MIGRATOR, dsn=fresh_db) as conn:
        apply_migrations(conn)
    with psycopg.connect(fresh_db, autocommit=True) as admin:
        for role in ("nacre_app", "nacre_verifier", "nacre_checkpointer", "nacre_keyadmin", "nacre_gc", "nacre_auth",
                     "nacre_principal_admin"):
            admin.execute(f"ALTER ROLE {role} LOGIN PASSWORD '{TEST_ROLE_PASSWORD}'")
    return {
        "admin": fresh_db,
        "app": dsn_with(fresh_db, user="nacre_app", password=TEST_ROLE_PASSWORD),
        "verifier": dsn_with(fresh_db, user="nacre_verifier", password=TEST_ROLE_PASSWORD),
        "checkpointer": dsn_with(fresh_db, user="nacre_checkpointer", password=TEST_ROLE_PASSWORD),
        "keyadmin": dsn_with(fresh_db, user="nacre_keyadmin", password=TEST_ROLE_PASSWORD),
        "gc": dsn_with(fresh_db, user="nacre_gc", password=TEST_ROLE_PASSWORD),
        "auth": dsn_with(fresh_db, user="nacre_auth", password=TEST_ROLE_PASSWORD),
        "principal_admin": dsn_with(fresh_db, user="nacre_principal_admin", password=TEST_ROLE_PASSWORD),
    }


# ---- shared: root keys, registered streams, scoped sessions (used by keys/ and ledger/ tests) ----
_seq = count(1)


@pytest.fixture
def test_role_password() -> str:
    """The test-only role password, for helpers that build their own databases (never import it from conftest)."""
    return TEST_ROLE_PASSWORD


@pytest.fixture
def provider(tmp_path):
    return LocalFileRootKeyProvider.initialise(tmp_path / "rootkeys")


@pytest.fixture
def streams(migrated_db):
    """An org and two project streams, registered (grants need registered scopes)."""
    org, a, b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with psycopg.connect(migrated_db["admin"]) as admin:
        for s, kind in ((org, "org"), (a, "project"), (b, "project")):
            admin.execute("INSERT INTO scopes.scopes (stream_id, kind, org_id, source_event_id) VALUES (%s, %s, %s, %s)",
                          (s, kind, org, uuid.uuid4()))
    return {"org": org, "a": a, "b": b, "dsn": migrated_db}


@pytest.fixture
def session(streams):
    """session(principal, read=[...], write=[...]) -> context manager yielding a ScopedSession."""
    @contextmanager
    def _session(principal, read=(), write=()):
        with psycopg.connect(streams["dsn"]["admin"]) as admin:
            for s in set(read) | set(write):
                admin.execute("""INSERT INTO scopes.scope_grants
                                 (principal_id, stream_id, org_id, can_read, can_append, source_event_id, source_seq)
                                 VALUES (%s, %s, %s, true, %s, %s, %s)""",
                              (principal, s, streams["org"], s in write, uuid.uuid4(), next(_seq)))
        with connect(DbRole.APP, dsn=streams["dsn"]["app"]) as conn, open_scoped_session(conn, principal) as s:
            yield s
    return _session


@pytest.fixture
def org(migrated_db, provider):
    """A bootstrapped org: returns (org_id, owner_principal, open(principal) -> scoped session ctx)."""
    from contextlib import contextmanager

    from nacre.core.db import DbRole, connect
    from nacre.scopes.bootstrap_org import bootstrap_org
    from nacre.scopes.open_scoped_session import open_scoped_session

    owner = uuid.uuid4()
    with connect(DbRole.MIGRATOR, dsn=migrated_db["admin"]) as admin:
        org_id = bootstrap_org(admin, provider, owner_principal_id=owner, idempotency_key=str(uuid.uuid4()))

    @contextmanager
    def open_(principal):
        with connect(DbRole.APP, dsn=migrated_db["app"]) as conn, open_scoped_session(conn, principal) as s:
            yield s
    return org_id, owner, open_
