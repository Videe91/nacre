"""Test infrastructure: a real Postgres (D-0006). DB tests FAIL, never skip, when it is down."""
import os

import psycopg
import pytest

DEFAULT_TEST_DSN = "postgresql://postgres:nacre_dev@127.0.0.1:54329/postgres"


@pytest.fixture(scope="session")
def pg_dsn() -> str:
    """Superuser DSN of the Docker test Postgres. Override with NACRE_TEST_DSN."""
    dsn = os.environ.get("NACRE_TEST_DSN", DEFAULT_TEST_DSN)
    try:
        psycopg.connect(dsn, connect_timeout=3).close()
    except psycopg.OperationalError as exc:
        pytest.fail(f"Test Postgres unreachable at {dsn!r}: run `docker compose up -d --wait`. ({exc})")
    return dsn
