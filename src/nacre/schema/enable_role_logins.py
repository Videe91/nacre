"""
Functionality: Enable LOGIN with a password on Nacre's cluster-wide NOLOGIN roles, for test, benchmark and
  evaluation databases, serialised across every process on the cluster.
Owns: the cluster-wide lock around ALTER ROLE and the role-password statements (the password is a bound literal,
  never formatted into SQL).
Public entry: enable_role_logins(), ROLE_LOGIN_LOCK
Decisions: D-0005, D-0006
Assumptions: none
Notes: Found 2026-10-02 (flaky-test hunt): roles are cluster-wide rows of pg_authid, and two processes running
  ALTER ROLE on the same role at the same time fail with "tuple concurrently updated" (reproduced deterministically
  in tests/schema/test_enable_role_logins.py). Every caller (tests/conftest.py, eval/run_exp0004.py, the benchmark
  and EXP-0003 scripts) goes through here.
  - The lock is a session-level advisory lock taken in the cluster's `postgres` database. Advisory locks are
    per-database, and test databases are many, so the lock must live in one shared database.
  - Each ALTER ROLE commits on its own while the lock is held, so no other caller's change can interleave.
  - This does NOT make concurrent runs with DIFFERENT passwords safe: the last writer wins (CURRENT F3). Tests,
    benchmarks and evaluation runs must not run concurrently with different passwords.
"""
from collections.abc import Iterable

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

ROLE_LOGIN_LOCK = 0x6E616372_65726F6C            # "nacreroL": one cluster-wide key for every caller


def enable_role_logins(admin_dsn: str, roles: Iterable[str], password: str) -> None:
    """ALTER ROLE <role> LOGIN PASSWORD <password> for each role, as a superuser, one caller at a time."""
    roles = list(roles)
    if not all(r.startswith("nacre_") and r.replace("_", "").isalnum() for r in roles):
        raise ValueError("only Nacre's roles may be enabled")
    info = conninfo_to_dict(admin_dsn)
    with psycopg.connect(make_conninfo(**(info | {"dbname": "postgres"})), autocommit=True) as lock, \
            psycopg.connect(admin_dsn, autocommit=True) as admin:
        lock.execute("SELECT pg_advisory_lock(%s)", (ROLE_LOGIN_LOCK,))
        try:
            for role in roles:
                admin.execute(sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(role),
                                                                               sql.Literal(password)))
        finally:
            lock.execute("SELECT pg_advisory_unlock(%s)", (ROLE_LOGIN_LOCK,))
