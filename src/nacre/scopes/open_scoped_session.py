"""
Functionality: The one door to the ledger: open a transaction scoped to one principal's access.
Owns: the pre-flight checks on the connection, setting the transaction-local scope settings, and
  guaranteeing they vanish at commit or rollback.
Public entry: open_scoped_session(), ScopedSession
Decisions: D-0003, D-0005
Assumptions: A-0011, A-0012
Notes: Steps (D-0005): check the connection → BEGIN → set nacre.principal → resolve_access →
  set nacre.read_streams / nacre.write_streams → yield → COMMIT (or ROLLBACK on error).
  Every setting uses set_config(..., is_local => true), i.e. SET LOCAL, so it dies with the
  transaction and a pooled or reused connection never carries it into the next request.
  Pre-flight refusals (D1; each closes a way RLS could be silently bypassed or weakened):
    - the connection is already inside a transaction (settings could outlive this scope, or
      nest under someone else's);
    - the connection's role is superuser or has BYPASSRLS (RLS would not apply at all);
    - the isolation level is not READ COMMITTED (D-0003 visibility order depends on it).
"""
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID

import psycopg
from psycopg import IsolationLevel
from psycopg.pq import TransactionStatus

from nacre.scopes.resolve_access import Access, resolve_access


class ScopeError(RuntimeError):
    """A scoped session cannot be opened safely on this connection."""


@dataclass(frozen=True, slots=True)
class ScopedSession:
    conn: psycopg.Connection
    access: Access


def _uuid_array(ids: frozenset[UUID]) -> str:
    return "{" + ",".join(sorted(str(i) for i in ids)) + "}"


@contextmanager
def open_scoped_session(conn: psycopg.Connection, principal_id: UUID) -> Iterator[ScopedSession]:
    """Yield a transaction in which RLS admits exactly this principal's streams."""
    if conn.info.transaction_status != TransactionStatus.IDLE:
        raise ScopeError("connection is already inside a transaction; open the scoped session first")
    if conn.isolation_level != IsolationLevel.READ_COMMITTED:
        raise ScopeError("scoped sessions require READ COMMITTED (use core.db.connect)")
    unsafe = conn.execute(
        "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user").fetchone()[0]
    conn.rollback()  # end the implicit transaction the check opened
    if unsafe:
        raise ScopeError("connection role bypasses row-level security; connect as nacre_app")
    with conn.transaction():
        conn.execute("SELECT set_config('nacre.principal', %s, true)", (str(principal_id),))
        access = resolve_access(conn, principal_id)
        conn.execute("SELECT set_config('nacre.read_streams', %s, true)", (_uuid_array(access.read_streams),))
        conn.execute("SELECT set_config('nacre.write_streams', %s, true)", (_uuid_array(access.write_streams),))
        yield ScopedSession(conn=conn, access=access)
