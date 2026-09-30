"""
Functionality: Open one Postgres connection with Nacre's fixed session settings.
Owns: DSN lookup per database role, explicit READ COMMITTED transactions, UTC session time zone.
Public entry: connect(), open_pool()
Decisions: D-0003, D-0005, D-0006
Assumptions: A-0011
Notes: No scope logic here: scope settings (SET LOCAL) belong to scopes/open_scoped_session.py.
  READ COMMITTED is set on the connection, so every BEGIN names it explicitly and a server or
  database default cannot change it; D-0003's visibility order depends on this.
  open_pool() (D-0006 amendment 1): a psycopg_pool ConnectionPool whose connections get exactly the same
  settings. Scope settings stay transaction-local (SET LOCAL via scopes/open_scoped_session.py), so a
  returned connection carries nothing to the next borrower; the pool also rolls back any open transaction
  on return. Tested in tests/core/test_db.py.
  DSNs come from NACRE_DSN_APP / NACRE_DSN_VERIFIER / NACRE_DSN_MIGRATOR unless passed in.
"""
import os
from enum import StrEnum

import psycopg
from psycopg import IsolationLevel
from psycopg_pool import ConnectionPool


class DbRole(StrEnum):
    APP = "app"
    VERIFIER = "verifier"
    MIGRATOR = "migrator"


def _dsn(role: DbRole, dsn: str | None) -> str:
    if dsn is None:
        var = f"NACRE_DSN_{role.value.upper()}"
        dsn = os.environ.get(var)
        if not dsn:
            raise LookupError(f"No DSN for database role {role.value!r}: set {var}")
    return dsn


def connect(role: DbRole, dsn: str | None = None) -> psycopg.Connection:
    """Open a connection for `role`: transactions are explicit and READ COMMITTED; time zone UTC."""
    conn = psycopg.connect(_dsn(role, dsn), autocommit=False, application_name=f"nacre-{role.value}",
                           options="-c TimeZone=UTC")
    conn.isolation_level = IsolationLevel.READ_COMMITTED
    return conn


def open_pool(role: DbRole, dsn: str | None = None, *, min_size: int = 1, max_size: int = 10) -> ConnectionPool:
    """A pool for `role` whose connections are configured exactly like connect()'s. Caller closes it."""
    def configure(conn: psycopg.Connection) -> None:
        conn.isolation_level = IsolationLevel.READ_COMMITTED

    return ConnectionPool(_dsn(role, dsn), min_size=min_size, max_size=max_size, open=True, configure=configure,
                          kwargs={"autocommit": False, "application_name": f"nacre-{role.value}",
                                  "options": "-c TimeZone=UTC"})
