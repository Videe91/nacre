"""
Functionality: Register a new scope (team, project, user or agent stream) in an org.
Owns: the register_scope config event in the org stream and its scope-registry row, written together.
Public entry: register_scope(), ScopeKind
Decisions: D-0005, D-0012
Assumptions: A-0014
Notes: Needs append on the org stream (RLS: scopes INSERT requires org_id in the write set). The event is
  the truth; the scopes row is its projection, carrying source_event_id so it can be rebuilt (D-0005).
  The caller chooses the new stream_id (a random UUID), so an exact retry is an identical request and
  returns the original event, and no second row is written (D-0012 part B). Org scopes are created
  only by bootstrap_org. Registering grants nobody access; see set_access. (D1)
"""
from enum import StrEnum
from uuid import UUID

from nacre.core.event import ActorKind, EventType, PayloadType, Source
from nacre.core.root_key_provider import RootKeyProvider
from nacre.ledger.append_event import AppendError, AppendRequest, AppendResult, Authorship, append_event
from nacre.scopes.open_scoped_session import ScopedSession


class ScopeKind(StrEnum):
    TEAM = "team"
    PROJECT = "project"
    USER = "user"
    AGENT = "agent"


def register_scope(session: ScopedSession, provider: RootKeyProvider, *, org_id: UUID, stream_id: UUID,
                   kind: ScopeKind, idempotency_key: str, parent_stream_id: UUID | None = None) -> AppendResult:
    """Record the scope in the org stream and in the scope registry, atomically, in the session's transaction."""
    if not isinstance(kind, ScopeKind):
        raise AppendError("kind must be a ScopeKind (org scopes come only from bootstrap_org)")
    if type(stream_id) is not UUID or stream_id == org_id:
        raise AppendError("stream_id must be a new random UUID, distinct from the org")
    result = append_event(session, provider, AppendRequest(
        stream_id=org_id, org_id=org_id, event_type=EventType.CONFIG_EVENT, payload_type=PayloadType.STRUCTURED,
        actor_kind=ActorKind.SYSTEM, actor_id=session.access.principal_id, source=Source.SYSTEM,
        authorship=Authorship.SCOPE_PRINCIPAL, idempotency_key=idempotency_key,
        content={"op": "register_scope", "stream_id": str(stream_id), "kind": kind.value,
                 "parent_stream_id": str(parent_stream_id) if parent_stream_id else None}))
    if result.created:
        session.conn.execute("""INSERT INTO scopes.scopes (stream_id, kind, org_id, parent_stream_id, source_event_id)
                                VALUES (%s, %s, %s, %s, %s)""",
                             (stream_id, kind.value, org_id, parent_stream_id, result.envelope.event_id))
    return result
