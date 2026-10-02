"""
Functionality: Record an observed outcome as capture evidence, with typed sections.
Owns: outcome validation (success, sections with closed roles, stakes tags), its `outcome_for` and optional
  `evaluates_prediction` references, and the append.
Public entry: record_outcome(), Section
Decisions: D-0018, D-0019, D-0002, D-0025
Assumptions: A-0026
Notes: Body = deterministic CBOR structured content (D-0008); every string is secret-stripped by append_event.
  The envelope's caused_by is set to the primary reference (D-0018). Stakes tags, where allowed, come from a closed
  set (D-0019) and are recorded, never inferred here.
  Sections carry a role, never an authority: authority is computed later from the envelope by
  capture/section_authority.py (D-0018, D3). A missing outcome is simply never recorded; absence is not failure
  (MNEXA ADR-0016), so there is no "unknown" placeholder outcome.
  D-0018 amendment 1: failing_checks names the tests/checks that actually failed (distinct, non-empty, <= 50), so
  D-0019 R4 can tell a predicted failure from a different one.
  D1: payload_type = structured. Integration results (CI, review, git) should be appended with
  Authorship.INTEGRATION_RESULT, which D-0012 trusts only for those sources with a structured payload.
  Addresses (D-0025 §3, the D-0018 amendment): optional caller-supplied `addresses`, validated by
  record_decision.check_addresses(); the key is in the body only when non-empty (old bytes unchanged).
"""
from dataclasses import dataclass
from uuid import UUID

from nacre.capture.record_decision import STAKES, CaptureError, check_addresses
from nacre.capture.section_authority import SECTION_ROLES
from nacre.capture.validate_refs import Ref, validate_refs
from nacre.core.event import ActorKind, EventType, Mode, PayloadType, Source
from nacre.core.root_key_provider import RootKeyProvider
from nacre.ledger.append_event import AppendRequest, AppendResult, Authorship, append_event
from nacre.scopes.open_scoped_session import ScopedSession


@dataclass(frozen=True)
class Section:
    role: str
    text: str


def record_outcome(session: ScopedSession, key_provider: RootKeyProvider, *, stream_id: UUID, actor_kind: ActorKind,
                   actor_id: UUID, source: Source, authorship: Authorship, idempotency_key: str, outcome_for: UUID,
                   success: bool | None, sections: tuple[Section, ...], evaluates_prediction: UUID | None = None,
                   stakes: tuple[str, ...] = (), failing_checks: tuple[str, ...] = (), cycle_id: UUID | None = None,
                   task_id: UUID | None = None, mode: Mode | None = None, actor_tool: str | None = None,
                   addresses: tuple[str, ...] = (), verified=None) -> AppendResult:
    """Append one `outcome` event for the decision or action `outcome_for`."""
    if success not in (True, False, None):
        raise CaptureError("success must be true, false or None")
    if not sections:
        raise CaptureError("an outcome needs at least one section")
    for s in sections:
        if not isinstance(s, Section) or s.role not in SECTION_ROLES or not isinstance(s.text, str) or not s.text.strip():
            raise CaptureError(f"sections need a role from {sorted(SECTION_ROLES)} and non-empty text")
    if set(stakes) - STAKES or len(set(stakes)) != len(stakes):
        raise CaptureError(f"stakes must be distinct tags from {sorted(STAKES)}")
    if len(failing_checks) > 50 or len(set(failing_checks)) != len(failing_checks) or any(
            not isinstance(c, str) or not c.strip() or len(c) > 200 for c in failing_checks):
        raise CaptureError("failing_checks are distinct non-empty names (<= 50, each <= 200 chars)")
    refs = [Ref("outcome_for", outcome_for)] + ([Ref("evaluates_prediction", evaluates_prediction)] if evaluates_prediction else [])
    body = {"success": success, "sections": [{"role": s.role, "text": s.text} for s in sections],
            "stakes": sorted(stakes), "failing_checks": list(failing_checks),
            "refs": validate_refs(session, stream_id, refs), **check_addresses(addresses)}
    return append_event(session, key_provider, verified=verified, request=AppendRequest(
        stream_id=stream_id, event_type=EventType.OUTCOME, payload_type=PayloadType.STRUCTURED, actor_kind=actor_kind,
        actor_id=actor_id, source=source, authorship=authorship, idempotency_key=idempotency_key, content=body,
        caused_by=outcome_for, cycle_id=cycle_id, task_id=task_id, mode=mode, actor_tool=actor_tool))
