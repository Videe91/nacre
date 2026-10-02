"""
Functionality: Record a lesson proposal grounded in an exact span of an outcome section.
Owns: checking the decision -> outcome ancestry, checking the span is exactly the section text at those offsets, the
  nucleus/qualifiers lie inside the support span (structured proposals), computing whether the support is a
  TRUSTED CORRECTION (D-0017 amendment 1), and the `lesson_proposed` memory event.
Public entry: propose_lesson(), ProposalError, Qualifier
Decisions: D-0017, D-0018, D-0020, D-0023
Assumptions: A-0026
Notes: A proposal is historical evidence with authority "proposal_only"; it is never recalled (D-0017). Admission
  rules (unique quote, closed world, atomicity) belong to the sleep pass (sleep/admit_propositions.py); this store
  re-checks only what promotion relies on: real ancestry and an exact span.
  grounded_in_trusted_correction = the section's role is `correction` AND capture/section_authority.py says it is
  authoritative (trusted by source and author). Only such proposals may promote alone (amendment 1).
  structure_status "unresolved" = support-first fallback (MNEXA 014 lesson): no nucleus, support span only.
  Ancestry (D-0020 amendment 1): the outcome must resolve to `decision_id`, directly or through its action's
  recorded link, by sleep/build_evidence_bundle.resolve_outcome_decision (the one definition of it). The proposal
  keeps decision_id/outcome_id only; the action id is not persisted here (no field for it).
"""
import uuid
from dataclasses import dataclass
from uuid import UUID

from nacre.capture.section_authority import section_authority
from nacre.core.event import ActorKind, EventType, PayloadType, Source
from nacre.core.root_key_provider import RootKeyProvider
from nacre.ledger.append_event import AppendRequest, Authorship, append_event
from nacre.ledger.read_stream import read_stream
from nacre.scopes.open_scoped_session import ScopedSession
from nacre.sleep.build_evidence_bundle import BundleError, resolve_outcome_decision
from nacre.stores.write_version import VERSION_ACTOR, normalize

QUALIFIER_TYPES = frozenset({"condition", "ordering", "scope", "negation"})


class ProposalError(ValueError):
    """The proposal is not grounded or its ancestry is not real."""


@dataclass(frozen=True)
class Qualifier:
    type: str
    text: str


def propose_lesson(session: ScopedSession, key_provider: RootKeyProvider, *, stream_id: UUID, decision_id: UUID,
                   outcome_id: UUID, section_index: int, span: tuple[int, int], nucleus: str | None,
                   qualifiers: tuple[Qualifier, ...] = (), run_id: UUID | None = None):
    """Append one `lesson_proposed` event; return its envelope. nucleus=None means a fallback (unresolved) proposal."""
    events = {e.envelope.event_id: e for e in read_stream(session, key_provider, stream_id)}
    d, o = events.get(decision_id), events.get(outcome_id)
    if d is None or d.envelope.event_type != EventType.DECISION:
        raise ProposalError("decision_id must be a decision of this stream")
    if o is None or o.envelope.event_type != EventType.OUTCOME or not isinstance(o.body, dict):
        raise ProposalError("outcome_id must be a readable outcome of this stream")
    oc = o.body["content"]
    try:
        linked = resolve_outcome_decision(o, events).decision_id == decision_id
    except BundleError:
        linked = False
    if not linked:
        raise ProposalError("the outcome is not an outcome of that decision")
    if type(section_index) is not int or not 0 <= section_index < len(oc["sections"]):
        raise ProposalError("section_index out of range")
    section = oc["sections"][section_index]
    start, end = span
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(section["text"]):
        raise ProposalError("span must lie inside the section text")
    support = section["text"][start:end]
    if not support.strip():
        raise ProposalError("the support span is empty")
    if nucleus is not None and (not nucleus.strip() or nucleus not in support):
        raise ProposalError("the nucleus must be a non-empty quote inside the support span")
    for q in qualifiers:
        if nucleus is None or q.type not in QUALIFIER_TYPES or not q.text.strip() or q.text not in support:
            raise ProposalError("qualifiers need a nucleus, a known type and a quote inside the support span")
    trusted = section["role"] == "correction" and section_authority(o.envelope, "correction", oc["success"]).authoritative
    content = {"op": "lesson_proposed", "authority": "proposal_only", "decision_id": str(decision_id),
               "outcome_id": str(outcome_id), "section_index": section_index, "section_role": section["role"],
               "span_start": start, "span_end": end, "support_text": support, "nucleus": nucleus,
               "qualifiers": [{"type": q.type, "text": q.text} for q in qualifiers],
               "structure_status": "structured" if nucleus is not None else "unresolved",
               "grounded_in_trusted_correction": trusted, "key": normalize(nucleus if nucleus is not None else support),
               "run_id": str(run_id) if run_id else None}
    return append_event(session, key_provider, AppendRequest(
        stream_id=stream_id, event_type=EventType.MEMORY_EVENT, payload_type=PayloadType.STRUCTURED,
        actor_kind=ActorKind.SYSTEM, actor_id=VERSION_ACTOR, source=Source.SYSTEM,
        authorship=Authorship.SCOPE_PRINCIPAL, idempotency_key=str(uuid.uuid4()), content=content,
        caused_by=outcome_id, cycle_id=run_id, sources=(decision_id, outcome_id))).envelope
