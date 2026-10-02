"""
Functionality: Record a contradiction proposal (a contradiction link) pinned to one exact belief version.
Owns: resolving the belief's current head (version and event), checking the contradicting decision has at least one
  real outcome (directly or through its action), checking a judged link's two spans and an explicit link's trusted
  correction, and the `contradiction_proposed` memory event.
Public entry: propose_contradiction(), JudgedSpans, ContradictionError, LINK_KINDS
Decisions: D-0017, D-0020, D-0023, D-0030
Assumptions: none
Notes: Proposal only (authority "proposal_only"); it never changes the belief (MNEXA ledger 38). Pinned to the head
  version seen NOW, so a later version is not contested by stale evidence (contest considers only proposals pinned to
  the current head). Three kinds (the `link` field; absent for the original direct kind, whose body is unchanged):
  - direct (no `link`): a caller-named decision and text. Grouped by normalised text in contest.
  - "judged" (D-0030): from the sleep pass's grounded relation judge. Records both spans (offsets + sha256 of the
    span text): the episode span inside the outcome's AUTHORITATIVE `correction` section (re-checked here against
    the outcome and capture/section_authority.py), and the belief span inside the target head's support_text; the
    judge call's `result` event id; and the head version event the judge saw, which must still be the head (else
    ContradictionError). Never against a superseded head. Grouped by target head in contest (owner condition 1).
  - "explicit" (D-0020 "Contradictions in Phase 2"): a TRUSTED `correction` event whose `correction_of` names a
    version event of this belief. No decision (decision_id null, outcome_ids empty); text = the correction's text.
    Never against a superseded head. contest_belief does not count it (quorum counts decisions; a gap for the
    owner, reported as open).
  D-0020 amendment 1: the decision's outcomes are every readable outcome of this stream that RESOLVES to it
  (sleep/build_evidence_bundle.resolve_outcome_decision), so an outcome recorded against an action counts.
  D-0023: keyed by its sources: the decision, its outcomes and the head (direct, judged); the correction and the head
  (explicit). The judge's result event is referenced in the content, not a source (its key covers the other
  candidates' contributors too).
  S2 seam (reversal, awaiting the owner): the link's identity is this event's id; a later withdrawal names it.
"""
import hashlib
import uuid
from dataclasses import dataclass
from uuid import UUID

from nacre.capture.section_authority import section_authority
from nacre.core.event import ActorKind, EventType, PayloadType, Source, Trust
from nacre.core.root_key_provider import RootKeyProvider
from nacre.ledger.append_event import AppendRequest, Authorship, append_event
from nacre.ledger.read_stream import read_stream
from nacre.scopes.open_scoped_session import ScopedSession
from nacre.sleep.build_evidence_bundle import BundleError, resolve_outcome_decision
from nacre.stores.write_version import VERSION_ACTOR, normalize, read_version_events

LINK_KINDS = ("judged", "explicit")


class ContradictionError(ValueError):
    """The contradiction has no real ancestry, no grounded span, or no belief to target."""


@dataclass(frozen=True)
class JudgedSpans:
    outcome_id: UUID
    section_index: int
    episode_span: tuple[int, int]
    belief_span: tuple[int, int]
    judge_result_event_id: UUID
    head_event_id: UUID              # the head version event the judge saw


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _resolved_outcomes(index: dict, decision_id: UUID) -> list[UUID]:
    out = []
    for e in index.values():
        if e.envelope.event_type != EventType.OUTCOME or not isinstance(e.body, dict):
            continue
        try:
            if resolve_outcome_decision(e, index).decision_id == decision_id:
                out.append(e.envelope.event_id)
        except BundleError:
            continue
    return out


def _judged(j: JudgedSpans, index, outcomes, head, text) -> dict:
    if j.head_event_id != head.envelope.event_id:
        raise ContradictionError("the judged head is no longer the belief's head")
    if j.outcome_id not in outcomes:
        raise ContradictionError("the judged outcome is not an outcome of the decision")
    o = index[j.outcome_id]
    secs = o.body["content"]["sections"]
    if type(j.section_index) is not int or not 0 <= j.section_index < len(secs):
        raise ContradictionError("section_index out of range")
    sec = secs[j.section_index]
    if sec["role"] != "correction" or not section_authority(o.envelope, "correction",
                                                            o.body["content"]["success"]).authoritative:
        raise ContradictionError("a judged link is grounded only in an authoritative correction section")
    (es, ee), (bs, be) = j.episode_span, j.belief_span
    support = head.body["content"]["content"]["support_text"]
    if not (0 <= es < ee <= len(sec["text"]) and sec["text"][es:ee] == text):
        raise ContradictionError("the episode span is not the text at those offsets")
    if not (0 <= bs < be <= len(support)) or not support[bs:be].strip():
        raise ContradictionError("the belief span must lie inside the head's support_text")
    return {"link": "judged", "judge_result_event_id": str(j.judge_result_event_id),
            "episode_span": {"outcome_id": str(j.outcome_id), "section_index": j.section_index, "start": es, "end": ee,
                             "sha256": _sha(text)},
            "belief_span": {"start": bs, "end": be, "sha256": _sha(support[bs:be])}}


def _explicit(correction_id: UUID, index, belief_object_id: UUID, versions, text) -> None:
    c = index.get(correction_id)
    if c is None or c.envelope.event_type != EventType.CORRECTION or not isinstance(c.body, dict):
        raise ContradictionError("correction_id must be a readable correction of this stream")
    if c.envelope.trust != Trust.TRUSTED:
        raise ContradictionError("only a trusted correction makes an explicit contradiction")
    named = {r["event_id"] for r in c.body["content"].get("refs", []) if r["rel"] == "correction_of"}
    if not named & {str(v.envelope.event_id) for v in versions}:
        raise ContradictionError("the correction does not name a version of this belief")
    if c.body["content"]["text"] != text:
        raise ContradictionError("an explicit contradiction's text is the correction's text")


def propose_contradiction(session: ScopedSession, key_provider: RootKeyProvider, *, stream_id: UUID,
                          belief_object_id: UUID, decision_id: UUID | None = None, text: str,
                          run_id: UUID | None = None, judged: JudgedSpans | None = None,
                          correction_id: UUID | None = None):
    """Append a `contradiction_proposed` event against the belief's current head; return its envelope."""
    if not isinstance(text, str) or not text.strip():
        raise ContradictionError("empty contradiction")
    if (correction_id is None) == (decision_id is None) or (judged is not None and correction_id is not None):
        raise ContradictionError("name a decision (direct or judged) or a correction (explicit), not both")
    versions = [v for v in read_version_events(session, key_provider, stream_id)
                if v.body["content"]["object_id"] == str(belief_object_id) and v.body["content"]["kind"] == "belief"]
    if not versions:
        raise ContradictionError("no belief with that id in this stream")
    head = versions[-1]
    if (judged or correction_id) and head.body["content"]["status"] == "superseded":
        raise ContradictionError("a superseded belief is not contradicted again")
    index = {e.envelope.event_id: e for e in read_stream(session, key_provider, stream_id)}
    extra, outcomes = {}, []
    if correction_id is not None:
        _explicit(correction_id, index, belief_object_id, versions, text)
        extra, sources = {"link": "explicit", "correction_id": str(correction_id)}, (correction_id,)
    else:
        d = index.get(decision_id)
        outcomes = _resolved_outcomes(index, decision_id)
        if d is None or d.envelope.event_type != EventType.DECISION or not outcomes:
            raise ContradictionError("the contradicting decision needs at least one observed outcome")
        if judged is not None:
            extra = _judged(judged, index, outcomes, head, text)
        sources = (decision_id, *outcomes)
    content = {"op": "contradiction_proposed", "authority": "proposal_only", "target_object_id": str(belief_object_id),
               "target_version": head.body["content"]["version"], "target_event_id": str(head.envelope.event_id),
               "decision_id": str(decision_id) if decision_id else None, "outcome_ids": [str(o) for o in outcomes],
               "text": text, "key": normalize(text), "run_id": str(run_id) if run_id else None, **extra}
    return append_event(session, key_provider, AppendRequest(
        stream_id=stream_id, event_type=EventType.MEMORY_EVENT, payload_type=PayloadType.STRUCTURED,
        actor_kind=ActorKind.SYSTEM, actor_id=VERSION_ACTOR, source=Source.SYSTEM,
        authorship=Authorship.SCOPE_PRINCIPAL, idempotency_key=str(uuid.uuid4()), content=content,
        caused_by=head.envelope.event_id, cycle_id=run_id,
        sources=tuple(dict.fromkeys((*sources, head.envelope.event_id))))).envelope
