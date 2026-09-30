"""
Functionality: Open one Postgres connection with Nacre's fixed session settings.
Owns: DSN lookup per database role, explicit READ COMMITTED transactions, UTC session time zone.
Public entry: connect()
Decisions: D-0003, D-0005, D-0006
Assumptions: A-0011
Notes: No scope logic here: scope settings (SET LOCAL) belong to scopes/open_scoped_session.py.
  READ COMMITTED is set on the connection, so every BEGIN names it explicitly and a server or
  database default cannot change it; D-0003's visibility order depends on this.
  No pool: psycopg_pool would be a new dependency. A-0011's pooled-reuse case is tested by
  reusing one connection across transactions.
  DSNs come from NACRE_DSN_APP / NACRE_DSN_VERIFIER / NACRE_DSN_MIGRATOR unless passed in.
"""
import os
from enum import StrEnum

import psycopg
from psycopg import IsolationLevel


class DbRole(StrEnum):
    APP = "app"
    VERIFIER = "verifier"
    MIGRATOR = "migrator"


def connect(role: DbRole, dsn: str | None = None) -> psycopg.Connection:
    """Open a connection for `role`: transactions are explicit and READ COMMITTED; time zone UTC."""
    if dsn is None:
        var = f"NACRE_DSN_{role.value.upper()}"
        dsn = os.environ.get(var)
        if not dsn:
            raise LookupError(f"No DSN for database role {role.value!r}: set {var}")
    conn = psycopg.connect(
        dsn,
        autocommit=False,
        application_name=f"nacre-{role.value}",
        options="-c TimeZone=UTC",
    )
    conn.isolation_level = IsolationLevel.READ_COMMITTED
    return conn
