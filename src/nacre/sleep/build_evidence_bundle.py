"""
Functionality: Build the evidence bundle for one flagged episode: the decision (as history) and the outcome's sections
  with their computed authority.
Owns: resolving a flagged outcome to its decision, numbering the sections, attaching each section's authority from
  the envelope, and rendering the bundle as the prompt's evidence block.
Public entry: build_evidence_bundle(), EvidenceBundle, BundleSection, BundleError
Decisions: D-0020, D-0018
Assumptions: A-0026
Notes: MNEXA lesson (seed 005): a failed decision is history, never truth; only authoritative corrections ground
  lessons. Sections are shown with their role and authority so the model sees context, but admission accepts quotes
  only from authoritative sections (sleep/admit_propositions.py). Authority is computed here from the envelope
  (D-0018), never from text, so the rendering can never be talked into granting it.
"""
from dataclasses import dataclass
from uuid import UUID

from nacre.capture.section_authority import section_authority
from nacre.core.event import EventType
from nacre.core.root_key_provider import RootKeyProvider
from nacre.ledger.read_stream import ReadEvent, read_stream
from nacre.scopes.open_scoped_session import ScopedSession


class BundleError(LookupError):
    """The flagged event is not a readable outcome with a readable decision."""


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

    def render(self) -> str:
        lines = ["DECISION (history: what was attempted; NOT evidence of truth):", self.decision_text, "",
                 f"OUTCOME (success = {self.success}):"]
        for s in self.sections:
            label = "AUTHORITATIVE" if s.authoritative else "context only, not authoritative"
            lines += [f"[S{s.index}] role={s.role} ({label})", s.text, ""]
        return "\n".join(lines).rstrip() + "\n"


def build_evidence_bundle(session: ScopedSession, key_provider: RootKeyProvider, stream_id: UUID, outcome_id: UUID,
                          index: dict[UUID, ReadEvent] | None = None) -> EvidenceBundle:
    """The bundle for the episode ending in `outcome_id` (pass `index` to avoid re-reading the stream)."""
    index = index or {e.envelope.event_id: e for e in read_stream(session, key_provider, stream_id)}
    o = index.get(outcome_id)
    if o is None or o.envelope.event_type != EventType.OUTCOME or not isinstance(o.body, dict):
        raise BundleError("not a readable outcome of this stream")
    c = o.body["content"]
    refs = [UUID(r["event_id"]) for r in c.get("refs", []) if r["rel"] == "outcome_for"]
    d = index.get(refs[0]) if refs else None
    if d is None or d.envelope.event_type != EventType.DECISION or not isinstance(d.body, dict):
        raise BundleError("the outcome's decision is not readable (or the outcome is for an action)")
    sections = tuple(BundleSection(i, s["role"], s["text"], section_authority(o.envelope, s["role"], c["success"]).authoritative)
                     for i, s in enumerate(c["sections"]))
    return EvidenceBundle(d.envelope.event_id, outcome_id, d.body["content"]["decision_text"], c["success"], sections,
                          (d.envelope.event_id, outcome_id))
