"""
Functionality: Grant, change or revoke one principal's access to one stream of an org.
Owns: the set_access config event in the org stream and its insert-only grant row, written together.
Public entry: set_access()
Decisions: D-0005, D-0012
Assumptions: A-0012
Notes: Needs append on the org stream (RLS: grant INSERT requires org_id in the write set). The row states
  the principal's FULL access to the stream as of the event's commit_seq (source_seq); the latest wins
  (scopes/resolve_access.py). A revoke is set_access(read=False, append=False). Append implies read,
  checked here and by the table CHECK. The stream must belong to the same org, enforced by a composite
  foreign key (migration 0006). An exact retry returns the original event and writes no second row.
"""
from uuid import UUID

from nacre.core.event import ActorKind, EventType, PayloadType, Source
from nacre.core.root_key_provider import RootKeyProvider
from nacre.ledger.append_event import AppendError, AppendRequest, AppendResult, Authorship, append_event
from nacre.scopes.open_scoped_session import ScopedSession


def set_access(session: ScopedSession, provider: RootKeyProvider, *, org_id: UUID, principal_id: UUID,
               stream_id: UUID, can_read: bool, can_append: bool, idempotency_key: str) -> AppendResult:
    """Record the principal's new access in the org stream and the grant projection, atomically."""
    if type(can_read) is not bool or type(can_append) is not bool:
        raise AppendError("can_read and can_append must be booleans")
    if can_append and not can_read:
        raise AppendError("append implies read (D-0005)")
    if type(principal_id) is not UUID or type(stream_id) is not UUID:
        raise AppendError("principal_id and stream_id must be UUIDs")
    result = append_event(session, provider, AppendRequest(
        stream_id=org_id, org_id=org_id, event_type=EventType.CONFIG_EVENT, payload_type=PayloadType.STRUCTURED,
        actor_kind=ActorKind.SYSTEM, actor_id=session.access.principal_id, source=Source.SYSTEM,
        authorship=Authorship.SCOPE_PRINCIPAL, idempotency_key=idempotency_key,
        content={"op": "set_access", "principal_id": str(principal_id), "stream_id": str(stream_id),
                 "can_read": can_read, "can_append": can_append}))
    if result.created:
        session.conn.execute("""INSERT INTO scopes.scope_grants (principal_id, stream_id, org_id, can_read, can_append,
                                source_event_id, source_seq) VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                             (principal_id, stream_id, org_id, can_read, can_append,
                              result.envelope.event_id, result.envelope.commit_seq))
    return result
