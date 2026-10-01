"""
Functionality: Turn one frozen MNEXA task family into Nacre capture events (decision + outcome with sections).
Owns: the pre-registered mapping of each frozen outcome format to D-0018 sections (EXP-0003 "Scenario per family"),
  removing MNEXA's role markers, deciding `success` for live first decisions with the ported grader, and appending
  both events through the capture functions.
Public entry: load_mnexa_family(), outcome_sections(), LoadedFamily, ROLE_TO_SECTION
Decisions: D-0016, D-0018
Assumptions: A-0026, A-0028
Notes: EVALUATION HARNESS ONLY (gate item 11, anti-shortcut guard (i)): this is the one place where task-author data
  becomes capture input; product code never sees task files or markers. Three frozen formats:
    003/004  a live first decision + `experience.feedback`:  status "EVALUATION: FAIL." (or PASS) + evaluation section
             holding the feedback; success = the set's own grader on that decision (P2);
    005-007  forced `candidate_decision` + `correction`:     status "EVALUATION: FAIL." + correction section
             (MNEXA seed_growth_005.py:316 built "EVALUATION: FAIL.\\nAUTHORITATIVE CORRECTION:\\n<correction>");
    008-016  forced `candidate_decision` + `raw_source` role regions, mapped by ROLE_TO_SECTION (EXP-0003 table);
             markers removed; no text may sit outside a region.
  The outcome is appended as a trusted integration result from `review` (EXP-0003: "trusted, source = review"); the
  decision as the agent's, from chat. Authority is then computed by capture/section_authority.py from the envelope.
"""
import re
import uuid
from dataclasses import dataclass
from uuid import UUID

from nacre.capture.record_decision import record_decision
from nacre.capture.record_outcome import Section, record_outcome
from nacre.core.event import ActorKind, Source
from nacre.core.root_key_provider import RootKeyProvider
from nacre.eval.grade_decision import grade_text
from nacre.ledger.append_event import Authorship
from nacre.scopes.open_scoped_session import ScopedSession

ROLE_TO_SECTION = {"status": "status", "failed_decision": "diagnostic", "authoritative_correction": "correction",
                   "operator_note": "operator_note", "diagnostic_metadata": "diagnostic"}
_REGION = re.compile(r"<<<ROLE:([a-z_]+)>>>(.*?)<<<END_ROLE:\1>>>", re.S)
HARNESS_AGENT = uuid.UUID("3c2b9f1e-7d4a-4c1b-9e2f-6a5d8b0c1f22")
HARNESS_REVIEWER = uuid.UUID("7e6d5c4b-3a2f-4e1d-8c0b-9a8f7e6d5c4b")


class MappingError(ValueError):
    """The family does not fit its pre-registered format."""


@dataclass(frozen=True)
class LoadedFamily:
    family_id: str
    decision_id: UUID
    outcome_id: UUID
    success: bool
    sections: tuple[Section, ...]


def outcome_sections(family: dict, set_number: int, decision_text: str | None = None) -> tuple[tuple[Section, ...], bool]:
    """(sections, success) for the family's first episode, per its frozen format."""
    if set_number in (3, 4):
        if decision_text is None:
            raise MappingError("sets 003/004 had a live first decision: pass its text")
        grader = family["experience"]["grader"] if set_number == 3 else family["task_grader"]
        success = grade_text(decision_text, grader)
        status = "EVALUATION: PASS." if success else "EVALUATION: FAIL."
        return (Section("status", status), Section("evaluation", family["experience"]["feedback"])), success
    if 5 <= set_number <= 7:
        return (Section("status", "EVALUATION: FAIL."), Section("correction", family["correction"])), False
    if 8 <= set_number <= 16:
        raw = family["raw_source"]
        if _REGION.sub("", raw).strip():
            raise MappingError(f"{family['id']}: text outside any role region")
        sections = []
        for role, text in _REGION.findall(raw):
            if role not in ROLE_TO_SECTION:
                raise MappingError(f"{family['id']}: unmapped role {role!r}")
            sections.append(Section(ROLE_TO_SECTION[role], text.strip()))
        return tuple(sections), False
    raise MappingError(f"no pre-registered mapping for set {set_number:03d}")


def load_mnexa_family(session: ScopedSession, key_provider: RootKeyProvider, stream_id: UUID, family: dict,
                      set_number: int, *, decision_text: str | None = None, cycle_id: UUID | None = None) -> LoadedFamily:
    """Append the family's first episode (decision, then outcome) to `stream_id`."""
    text = decision_text if set_number in (3, 4) else family["candidate_decision"]
    sections, success = outcome_sections(family, set_number, text)
    d = record_decision(session, key_provider, stream_id=stream_id, actor_kind=ActorKind.AGENT, actor_id=HARNESS_AGENT,
                        source=Source.CHAT, authorship=Authorship.SCOPE_PRINCIPAL, idempotency_key=str(uuid.uuid4()),
                        decision_text=text, cycle_id=cycle_id).envelope
    o = record_outcome(session, key_provider, stream_id=stream_id, actor_kind=ActorKind.SYSTEM,
                       actor_id=HARNESS_REVIEWER, source=Source.REVIEW, authorship=Authorship.INTEGRATION_RESULT,
                       idempotency_key=str(uuid.uuid4()), outcome_for=d.event_id, success=success, sections=sections,
                       cycle_id=cycle_id).envelope
    return LoadedFamily(family["id"], d.event_id, o.event_id, success, sections)
