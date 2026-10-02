"""Tests for schema/enable_role_logins.py: concurrent ALTER ROLE on a cluster-wide role fails with "tuple
concurrently updated" (positive control); through the helper, a second caller waits for the cluster-wide lock and
succeeds. Deterministic interleaving: the first writer holds its uncommitted change while the second starts."""
import threading
import time

import psycopg
import pytest

from nacre.schema.enable_role_logins import ROLE_LOGIN_LOCK, enable_role_logins
from psycopg.conninfo import conninfo_to_dict, make_conninfo

ROLE = "nacre_gc"


def _postgres_db(dsn):
    return make_conninfo(**(conninfo_to_dict(dsn) | {"dbname": "postgres"}))


def _second_writer(fn):
    out = []

    def run():
        try:
            fn()
            out.append("ok")
        except Exception as exc:  # noqa: BLE001 - reported below
            out.append(f"{type(exc).__name__}: {str(exc).splitlines()[0]}")
    t = threading.Thread(target=run)
    t.start()
    return t, out


def test_a_bare_concurrent_alter_role_fails(pg_dsn, test_role_password):
    first = psycopg.connect(pg_dsn)
    first.execute(f"ALTER ROLE {ROLE} NOLOGIN")                  # uncommitted
    t, out = _second_writer(lambda: psycopg.connect(pg_dsn, autocommit=True).execute(f"ALTER ROLE {ROLE} NOLOGIN"))
    time.sleep(0.5)
    first.commit()
    first.close()
    t.join(10)
    assert out and "tuple concurrently updated" in out[0]       # the race the helper exists for
    enable_role_logins(pg_dsn, [ROLE], test_role_password)       # restore


def test_the_helper_waits_for_the_cluster_wide_lock_and_succeeds(pg_dsn, test_role_password):
    with psycopg.connect(_postgres_db(pg_dsn), autocommit=True) as lock:
        lock.execute("SELECT pg_advisory_lock(%s)", (ROLE_LOGIN_LOCK,))     # a first caller, mid-change
        first = psycopg.connect(pg_dsn)
        first.execute(f"ALTER ROLE {ROLE} NOLOGIN")
        t, out = _second_writer(lambda: enable_role_logins(pg_dsn, [ROLE], test_role_password))
        time.sleep(0.5)
        assert t.is_alive() and out == []                         # waiting on the lock, not racing
        first.commit()
        first.close()
        lock.execute("SELECT pg_advisory_unlock(%s)", (ROLE_LOGIN_LOCK,))
    t.join(10)
    assert out == ["ok"]


def test_only_nacre_roles_and_no_sql_injection(pg_dsn):
    for bad in (["postgres"], ["nacre_app; DROP ROLE x"]):
        with pytest.raises(ValueError):
            enable_role_logins(pg_dsn, bad, "x")
