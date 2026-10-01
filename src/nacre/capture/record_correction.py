"""
Functionality: Record a correction of an earlier event as capture evidence.
Owns: correction validation (text, optional scope), its `correction_of` reference, and the append.
Public entry: record_correction()
Decisions: D-0018, D-0019, D-0002
Assumptions: none
Notes: Body = deterministic CBOR structured content (D-0008); every string is secret-stripped by append_event.
  The envelope's caused_by is set to the primary reference (D-0018). Stakes tags, where allowed, come from a closed
  set (D-0019) and are recorded, never inferred here.
  Nothing is edited: a correction is a new event pointing back (D-0002). A trusted correction from a person is a
  write-gate signal (D-0019 "statement"). A correction of a belief-version event can lead to a contradiction proposal
  (D-0020 "Contradictions in Phase 2").
"""
from uuid import UUID

from nacre.capture.record_decision import CaptureError
from nacre.capture.validate_refs import Ref, validate_refs
from nacre.core.event import ActorKind, EventType, Mode, PayloadType, Source
from nacre.core.root_key_provider import RootKeyProvider
from nacre.ledger.append_event import AppendRequest, AppendResult, Authorship, append_event
from nacre.scopes.open_scoped_session import ScopedSession


def record_correction(session: ScopedSession, key_provider: RootKeyProvider, *, stream_id: UUID, actor_kind: ActorKind,
                      actor_id: UUID, source: Source, authorship: Authorship, idempotency_key: str, correction_of: UUID,
                      text: str, scope_of_correction: str | None = None, cycle_id: UUID | None = None,
                      task_id: UUID | None = None, mode: Mode | None = None) -> AppendResult:
    """Append one `correction` event pointing at `correction_of`."""
    if not isinstance(text, str) or not text.strip():
        raise CaptureError("correction text must be non-empty")
    if scope_of_correction is not None and (not isinstance(scope_of_correction, str) or not scope_of_correction.strip()):
        raise CaptureError("scope_of_correction, when given, must be non-empty text")
    body = {"text": text, "scope_of_correction": scope_of_correction,
            "refs": validate_refs(session, stream_id, [Ref("correction_of", correction_of)])}
    return append_event(session, key_provider, AppendRequest(
        stream_id=stream_id, event_type=EventType.CORRECTION, payload_type=PayloadType.STRUCTURED, actor_kind=actor_kind,
        actor_id=actor_id, source=source, authorship=authorship, idempotency_key=idempotency_key, content=body,
        caused_by=correction_of, cycle_id=cycle_id, task_id=task_id, mode=mode))
