"""
Functionality: A key-administration transaction: key operations as nacre_keyadmin, audit appends as nacre_app, one
  atomic commit.
Owns: the connection checks, the role switching inside one transaction, and the app-side settings for the appends.
Public entry: keyadmin_transaction(), KeyAdminTx
Decisions: D-0005, D-0014
Assumptions: A-0012
Notes: The connection must be logged in as nacre_keyadmin (migration 0007). Inside the transaction:
    tx.as_keyadmin()                     -> key and registry statements, as nacre_keyadmin (RLS: all rows)
    tx.as_app(principal, read, write)    -> append_event under RLS with transaction-local settings for exactly those
                                            streams, as the requesting principal (D-0014: events are written as
                                            the requester)
  Everything commits together or not at all, so a destruction is never unrecorded and a record never describes a
  destruction that did not happen (D-0014 owner condition). Settings are SET LOCAL, so nothing leaks after the
  transaction (D-0005). tx.session is the ScopedSession for append_event / read_stream while in app mode.
"""
from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

import psycopg
from psycopg.pq import TransactionStatus

from nacre.scopes.open_scoped_session import ScopedSession
from nacre.scopes.resolve_access import Access


class KeyAdminError(RuntimeError):
    """The key-administration transaction cannot run safely on this connection."""


class KeyAdminTx:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn
        self.session: ScopedSession | None = None

    def as_keyadmin(self) -> psycopg.Connection:
        self.conn.execute("SET LOCAL ROLE nacre_keyadmin")
        self.session = None
        return self.conn

    def as_app(self, principal: UUID, read: set[UUID], write: set[UUID]) -> ScopedSession:
        if not set(write) <= set(read):
            raise KeyAdminError("append implies read")
        self.conn.execute("SET LOCAL ROLE nacre_app")
        for name, ids in (("nacre.read_streams", read), ("nacre.write_streams", write)):
            self.conn.execute("SELECT set_config(%s, %s, true)", (name, "{" + ",".join(sorted(map(str, ids))) + "}"))
        self.conn.execute("SELECT set_config('nacre.principal', %s, true)", (str(principal),))
        self.session = ScopedSession(conn=self.conn, access=Access(principal, frozenset(read), frozenset(write)))
        return self.session


@contextmanager
def keyadmin_transaction(conn: psycopg.Connection) -> Iterator[KeyAdminTx]:
    """One atomic key-administration transaction on a nacre_keyadmin connection."""
    if conn.info.transaction_status != TransactionStatus.IDLE:
        raise KeyAdminError("start outside a transaction")
    user = conn.execute("SELECT session_user").fetchone()[0]
    conn.rollback()
    if user != "nacre_keyadmin":
        raise KeyAdminError(f"key administration needs a nacre_keyadmin login, not {user}")
    with conn.transaction():
        tx = KeyAdminTx(conn)
        tx.as_keyadmin()
        yield tx
