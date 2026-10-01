"""
Functionality: Record a prediction about a decision's outcome as capture evidence.
Owns: prediction validation (expected outcome, predictor, optional confidence), its `response_to` reference, and the
  append.
Public entry: record_prediction()
Decisions: D-0018, D-0019, D-0002
Assumptions: none
Notes: Body = deterministic CBOR structured content (D-0008); every string is secret-stripped by append_event.
  The envelope's caused_by is set to the primary reference (D-0018). Stakes tags, where allowed, come from a closed
  set (D-0019) and are recorded, never inferred here.
  D1: confidence is an integer percentage 0-100 because the CBOR subset has no floats (D-0008).
  expected_success (true/false/None) is what D-0019's surprise rule compares with the outcome's `success`.
  D-0018 amendment 1: expected_failing_check names the specific test/check expected to fail (only with
  expected_success = false). Without it a predicted failure is "vague" and never suppresses a flag (D-0019 R4).
"""
from uuid import UUID

from nacre.capture.record_decision import CaptureError
from nacre.capture.validate_refs import Ref, validate_refs
from nacre.core.event import ActorKind, EventType, Mode, PayloadType, Source
from nacre.core.root_key_provider import RootKeyProvider
from nacre.ledger.append_event import AppendRequest, AppendResult, Authorship, append_event
from nacre.scopes.open_scoped_session import ScopedSession


def record_prediction(session: ScopedSession, key_provider: RootKeyProvider, *, stream_id: UUID, actor_kind: ActorKind,
                      actor_id: UUID, source: Source, authorship: Authorship, idempotency_key: str, decision_id: UUID,
                      expected_outcome: str, expected_success: bool | None, predictor: str = "agent",
                      confidence_pct: int | None = None, expected_failing_check: str | None = None,
                      cycle_id: UUID | None = None, task_id: UUID | None = None, mode: Mode | None = None) -> AppendResult:
    """Append one `prediction` event that responds to `decision_id`."""
    if not isinstance(expected_outcome, str) or not expected_outcome.strip():
        raise CaptureError("expected_outcome must be non-empty text")
    if expected_success not in (True, False, None):
        raise CaptureError("expected_success must be true, false or None")
    if predictor not in ("agent", "predictor"):
        raise CaptureError("predictor must be 'agent' or 'predictor'")
    if confidence_pct is not None and (type(confidence_pct) is not int or not 0 <= confidence_pct <= 100):
        raise CaptureError("confidence_pct must be an integer 0-100")
    if expected_failing_check is not None and (expected_success is not False or not isinstance(expected_failing_check, str)
                                               or not expected_failing_check.strip() or len(expected_failing_check) > 200):
        raise CaptureError("expected_failing_check is a non-empty name (<= 200 chars), only for an expected failure")
    refs = validate_refs(session, stream_id, [Ref("response_to", decision_id)])
    if session.conn.execute("SELECT event_type FROM ledger.events WHERE event_id = %s", (decision_id,)).fetchone()[0] != "decision":
        raise CaptureError("a prediction responds to a decision")
    body = {"expected_outcome": expected_outcome, "expected_success": expected_success, "predictor": predictor,
            "confidence_pct": confidence_pct, "expected_failing_check": expected_failing_check, "refs": refs}
    return append_event(session, key_provider, AppendRequest(
        stream_id=stream_id, event_type=EventType.PREDICTION, payload_type=PayloadType.STRUCTURED, actor_kind=actor_kind,
        actor_id=actor_id, source=source, authorship=authorship, idempotency_key=idempotency_key, content=body,
        caused_by=decision_id, cycle_id=cycle_id, task_id=task_id, mode=mode))
