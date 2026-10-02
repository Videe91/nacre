"""
Functionality: Record a dispatched action as capture evidence.
Owns: action validation (kind, description, stakes tags), its `execution_of` reference to a decision, and the append.
Public entry: record_action()
Decisions: D-0018, D-0019, D-0002
Assumptions: none
Notes: Body = deterministic CBOR structured content (D-0008); every string is secret-stripped by append_event.
  The envelope's caused_by is set to the primary reference (D-0018). Stakes tags, where allowed, come from a closed
  set (D-0019) and are recorded, never inferred here.
  The action boundary is dispatch, not attempt (MNEXA ADR-0016): call this only once the action was sent, so
  `dispatched` is always true.
"""
from uuid import UUID

from nacre.capture.record_decision import STAKES, CaptureError
from nacre.capture.validate_refs import Ref, validate_refs
from nacre.core.event import ActorKind, EventType, Mode, PayloadType, Source
from nacre.core.root_key_provider import RootKeyProvider
from nacre.ledger.append_event import AppendRequest, AppendResult, Authorship, append_event
from nacre.scopes.open_scoped_session import ScopedSession


def record_action(session: ScopedSession, key_provider: RootKeyProvider, *, stream_id: UUID, actor_kind: ActorKind,
                  actor_id: UUID, source: Source, authorship: Authorship, idempotency_key: str, decision_id: UUID,
                  action_kind: str, description: str, stakes: tuple[str, ...] = (), cycle_id: UUID | None = None,
                  task_id: UUID | None = None, mode: Mode | None = None, actor_tool: str | None = None, verified=None) -> AppendResult:
    """Append one `action` event that executes `decision_id`."""
    if not isinstance(action_kind, str) or not action_kind or not isinstance(description, str) or not description.strip():
        raise CaptureError("action_kind and description must be non-empty text")
    if set(stakes) - STAKES or len(set(stakes)) != len(stakes):
        raise CaptureError(f"stakes must be distinct tags from {sorted(STAKES)}")
    body = {"action_kind": action_kind, "description": description, "dispatched": True, "stakes": sorted(stakes),
            "refs": validate_refs(session, stream_id, [Ref("execution_of", decision_id)])}
    return append_event(session, key_provider, verified=verified, request=AppendRequest(
        stream_id=stream_id, event_type=EventType.ACTION, payload_type=PayloadType.STRUCTURED, actor_kind=actor_kind,
        actor_id=actor_id, source=source, authorship=authorship, idempotency_key=idempotency_key, content=body,
        caused_by=decision_id, cycle_id=cycle_id, task_id=task_id, mode=mode, actor_tool=actor_tool))
