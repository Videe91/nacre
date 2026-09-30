"""
Functionality: Resolve which streams a principal may read and append to, from its current grants.
Owns: the latest-grant-wins rule, the Access value type, the append-implies-read guarantee.
Public entry: resolve_access(), Access
Decisions: D-0005
Assumptions: A-0012
Notes: Reads scopes.scope_grants, an insert-only projection. Per (principal, stream), the row with
  the highest source_seq wins; a row with both flags false is a revoke. There is no inheritance:
  access to an org does not imply its projects (D-0005 grants are explicit).
  RLS lets a principal read only its own grants, via the transaction-local `nacre.principal`
  setting. The caller (scopes/open_scoped_session.py) sets it first; this function checks it
  matches, so it cannot be asked about one principal while scoped as another.
  Deleted scopes are not filtered here; scope deletion (#21) records revokes. (D1)
"""
from dataclasses import dataclass
from uuid import UUID

import psycopg


@dataclass(frozen=True, slots=True)
class Access:
    principal_id: UUID
    read_streams: frozenset[UUID]
    write_streams: frozenset[UUID]


class AccessError(RuntimeError):
    """Access could not be resolved safely."""


def resolve_access(conn: psycopg.Connection, principal_id: UUID) -> Access:
    """The principal's readable and appendable streams, as of now, inside the caller's transaction."""
    scoped_as = conn.execute("SELECT current_setting('nacre.principal', true)").fetchone()[0]
    if scoped_as != str(principal_id):
        raise AccessError("nacre.principal must be set to this principal before resolving its access")
    rows = conn.execute("""
        SELECT DISTINCT ON (stream_id) stream_id, can_read, can_append
          FROM scopes.scope_grants
         WHERE principal_id = %s
         ORDER BY stream_id, source_seq DESC""", (principal_id,)).fetchall()
    read = frozenset(s for s, can_read, _ in rows if can_read)
    write = frozenset(s for s, _, can_append in rows if can_append)
    if not write <= read:  # guaranteed by a CHECK on every row; asserted because RLS depends on it
        raise AccessError("append without read in grants")
    return Access(principal_id=principal_id, read_streams=read, write_streams=write)
