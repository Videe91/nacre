"""
Functionality: Build the evidence bundle for one flagged episode: the decision (as history) and the outcome's sections
  with their computed authority.
Owns: resolving a flagged outcome to its decision (directly, or through its action's recorded link: D-0020
  amendment 1), numbering the sections, attaching each section's authority from the envelope, and rendering the
  bundle as the prompt's evidence block.
Public entry: build_evidence_bundle(), resolve_outcome_decision(), Resolution, EvidenceBundle, BundleSection,
  BundleError, UnlinkedAction
Decisions: D-0020, D-0018
Assumptions: A-0026
Notes: MNEXA lesson (seed 005): a failed decision is history, never truth; only authoritative corrections ground
  lessons. Sections are shown with their role and authority so the model sees context, but admission accepts quotes
  only from authoritative sections (sleep/admit_propositions.py). Authority is computed here from the envelope
  (D-0018), never from text, so the rendering can never be talked into granting it.
  D-0020 amendment 1 (outcome recorded against an ACTION): the decision is the one named by the action payload's
  single `execution_of` reference (D-0018), looked up in THIS stream's read only. D1 choices:
    - only the payload reference counts; the envelope's caused_by (a mirror of it) is never a fallback;
    - zero or several `execution_of` references, an unreadable (shredded) action, a target that is not in this
      stream's read (missing, other stream), or a target that is not a decision -> UnlinkedAction. Nothing is ever
      inferred (timing, text, neighbours): no other event is consulted.
    - resolve_outcome_decision() checks the LINK (types, and a readable action/outcome to read it from), not the
      decision's body, so the stores' ancestry checks keep their behaviour for erased decisions. The bundle needs the
      decision text: an erased decision is a BundleError when direct, and UnlinkedAction when through an action.
    - the action contributes no text: the rendered prompt and the model-call sources stay (decision, outcome), so an
      action-linked episode is consolidated exactly like a direct one; the action id is carried as `action_id`.
  Also used by stores/propose_lesson.py and stores/promote_if_supported.py as the one definition of
  decision -> outcome ancestry.
"""
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from nacre.capture.section_authority import section_authority
from nacre.core.event import EventType
from nacre.core.root_key_provider import RootKeyProvider
from nacre.ledger.read_stream import ReadEvent, read_stream
from nacre.scopes.open_scoped_session import ScopedSession


class BundleError(LookupError):
    """The flagged event is not a readable outcome with a readable decision."""


class UnlinkedAction(BundleError):
    """The outcome is for an action with no readable decision link (D-0020 amendment 1: counted, never inferred)."""


@dataclass(frozen=True)
class Resolution:
    decision_id: UUID
    action_id: UUID | None          # set when the outcome was recorded against an action


@dataclass(frozen=True)
class BundleSection:
    index: int
    role: str
    text: str
    authoritative: bool


@dataclass(frozen=True)
class EvidenceBundle:
    decision_id: UUID
    outcome_id: UUID
    decision_text: str
    success: bool | None
    sections: tuple[BundleSection, ...]
    source_event_ids: tuple[UUID, ...]
    action_id: UUID | None = None

    def render(self) -> str:
        lines = ["DECISION (history: what was attempted; NOT evidence of truth):", self.decision_text, "",
                 f"OUTCOME (success = {self.success}):"]
        for s in self.sections:
            label = "AUTHORITATIVE" if s.authoritative else "context only, not authoritative"
            lines += [f"[S{s.index}] role={s.role} ({label})", s.text, ""]
        return "\n".join(lines).rstrip() + "\n"


def _refs(event: ReadEvent, rel: str) -> list[UUID]:
    return [UUID(r["event_id"]) for r in event.body["content"].get("refs", []) if r["rel"] == rel]


def _is_decision(event: ReadEvent | None) -> bool:
    return event is not None and event.envelope.event_type == EventType.DECISION


def resolve_outcome_decision(outcome: ReadEvent, index: Mapping[UUID, ReadEvent]) -> Resolution:
    """The decision `outcome` is for, from recorded references only. `index` is the outcome's own stream."""
    if outcome.envelope.event_type != EventType.OUTCOME or not isinstance(outcome.body, dict):
        raise BundleError("not a readable outcome of this stream")
    targets = _refs(outcome, "outcome_for")
    target = index.get(targets[0]) if len(targets) == 1 else None
    if target is not None and target.envelope.event_type == EventType.ACTION:
        if not isinstance(target.body, dict):
            raise UnlinkedAction("the outcome's action is not readable")
        links = _refs(target, "execution_of")
        decision = index.get(links[0]) if len(links) == 1 else None
        if not _is_decision(decision):
            raise UnlinkedAction("the outcome's action has no readable decision link in this stream")
        return Resolution(decision.envelope.event_id, target.envelope.event_id)
    if not _is_decision(target):
        raise BundleError("the outcome is not for a decision or action of this stream")
    return Resolution(target.envelope.event_id, None)


def build_evidence_bundle(session: ScopedSession, key_provider: RootKeyProvider, stream_id: UUID, outcome_id: UUID,
                          index: dict[UUID, ReadEvent] | None = None) -> EvidenceBundle:
    """The bundle for the episode ending in `outcome_id` (pass `index` to avoid re-reading the stream)."""
    index = index or {e.envelope.event_id: e for e in read_stream(session, key_provider, stream_id)}
    o = index.get(outcome_id)
    if o is None:
        raise BundleError("not a readable outcome of this stream")
    r = resolve_outcome_decision(o, index)
    d, c = index[r.decision_id], o.body["content"]
    if not isinstance(d.body, dict):
        raise (UnlinkedAction if r.action_id else BundleError)("the outcome's decision is not readable (erased)")
    sections = tuple(BundleSection(i, s["role"], s["text"], section_authority(o.envelope, s["role"], c["success"]).authoritative)
                     for i, s in enumerate(c["sections"]))
    return EvidenceBundle(r.decision_id, outcome_id, d.body["content"]["decision_text"], c["success"], sections,
                          (r.decision_id, outcome_id), r.action_id)
