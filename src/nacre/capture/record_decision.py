"""
Functionality: Record a decision as capture evidence.
Owns: decision validation (text, kind, optional decided_from and context hash, stakes tags), its typed references,
  and the append.
Public entry: record_decision(), CaptureError, STAKES
Decisions: D-0018, D-0019, D-0002
Assumptions: none
Notes: Body = deterministic CBOR structured content (D-0008); every string is secret-stripped by append_event.
  The envelope's caused_by is set to the primary reference (D-0018). Stakes tags, where allowed, come from a closed
  set (D-0019) and are recorded, never inferred here.
  decided_from (the ContextAssembled event a decision was made from) is optional in Phase 2 (D-0018), because
  recall arrives in Phase 3. When given, it must be a committed event of the same stream. reasoning_owner is always
  "external": Nacre records decisions, it never makes them (MNEXA ADR-0017).
"""
from uuid import UUID

from nacre.capture.validate_refs import Ref, validate_refs
from nacre.core.event import ActorKind, EventType, Mode, PayloadType, Source
from nacre.core.root_key_provider import RootKeyProvider
from nacre.ledger.append_event import AppendRequest, AppendResult, Authorship, append_event
from nacre.scopes.open_scoped_session import ScopedSession

STAKES = frozenset({"money", "client", "production", "irreversible"})


class CaptureError(ValueError):
    """The capture event is not valid."""


def record_decision(session: ScopedSession, key_provider: RootKeyProvider, *, stream_id: UUID, actor_kind: ActorKind,
                    actor_id: UUID, source: Source, authorship: Authorship, idempotency_key: str, decision_text: str,
                    decision_kind: str = "task_response", decided_from: UUID | None = None,
                    context_evidence_sha256: str | None = None, refs: tuple[Ref, ...] = (),
                    stakes: tuple[str, ...] = (), cycle_id: UUID | None = None, task_id: UUID | None = None,
                    mode: Mode | None = None, actor_model: str | None = None,
                    actor_model_version: str | None = None) -> AppendResult:
    """Append one `decision` event."""
    if not isinstance(decision_text, str) or not decision_text.strip():
        raise CaptureError("decision_text must be non-empty text")
    if not isinstance(decision_kind, str) or not decision_kind:
        raise CaptureError("decision_kind must be non-empty text")
    if set(stakes) - STAKES or len(set(stakes)) != len(stakes):
        raise CaptureError(f"stakes must be distinct tags from {sorted(STAKES)}")
    if context_evidence_sha256 is not None and (decided_from is None or len(context_evidence_sha256) != 64):
        raise CaptureError("context_evidence_sha256 needs decided_from and is a 64-hex SHA-256")
    if decided_from is not None and session.conn.execute(
            "SELECT 1 FROM ledger.events WHERE event_id = %s AND stream_id = %s", (decided_from, stream_id)).fetchone() is None:
        raise CaptureError("decided_from must be a committed event of this stream")
    body = {"decision_text": decision_text, "decision_kind": decision_kind, "reasoning_owner": "external",
            "decided_from": str(decided_from) if decided_from else None,
            "context_evidence_sha256": context_evidence_sha256, "stakes": sorted(stakes),
            "refs": validate_refs(session, stream_id, list(refs))}
    return append_event(session, key_provider, AppendRequest(
        stream_id=stream_id, event_type=EventType.DECISION, payload_type=PayloadType.STRUCTURED, actor_kind=actor_kind,
        actor_id=actor_id, source=source, authorship=authorship, idempotency_key=idempotency_key, content=body,
        caused_by=decided_from or (refs[0].event_id if refs else None), cycle_id=cycle_id, task_id=task_id, mode=mode,
        actor_model=actor_model, actor_model_version=actor_model_version))
