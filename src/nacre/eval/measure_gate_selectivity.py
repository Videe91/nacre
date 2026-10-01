"""
Functionality: Measure the write gate's selectivity on the frozen routine episode set (Phase 2 gate item 7, D-0019 R3).
Owns: loading one routine episode as capture events (decision, prediction, outcome evaluating it), running the gate,
  the flag-rate report, and the item-7 verdict (100% recall AND flag rate <= ceiling).
Public entry: measure_selectivity(), load_routine_episode(), item7_passes(), SelectivityReport, CEILING_PERMILLE
Decisions: D-0019, D-0016, D-0018
Assumptions: A-0028
Notes: EVALUATION HARNESS ONLY. The ceiling (50 per mille = 5%) and the set (routine_episodes_v1, sha256 cad974a7...)
  were pre-registered in D-0019 R3 before any measurement; changing either is a new registration, never an edit.
  A gate that flags everything must fail item 7: item7_passes() fails it on the flag rate even with perfect recall.
"""
import uuid
from dataclasses import dataclass
from uuid import UUID

from nacre.capture.record_decision import record_decision
from nacre.capture.record_outcome import Section, record_outcome
from nacre.capture.record_prediction import record_prediction
from nacre.core.event import ActorKind, Source
from nacre.core.root_key_provider import RootKeyProvider
from nacre.gate.flag_events import flag_events
from nacre.ledger.append_event import Authorship
from nacre.scopes.open_scoped_session import ScopedSession

CEILING_PERMILLE = 50
_AGENT = uuid.UUID("1b0e5d2c-9a8f-4e7d-b6c5-4a3b2c1d0e9f")
_REPORTER = uuid.UUID("2c1f6e3d-0b9a-4f8e-a7d6-5b4c3d2e1f0a")
_AUTHORSHIP = {"integration_result": Authorship.INTEGRATION_RESULT, "external": Authorship.EXTERNAL}


@dataclass(frozen=True)
class SelectivityReport:
    episodes: int
    flagged: int
    flagged_ids: tuple[str, ...]

    @property
    def rate_permille(self) -> int:
        return (self.flagged * 1000) // self.episodes if self.episodes else 0


def load_routine_episode(session: ScopedSession, key_provider: RootKeyProvider, stream_id: UUID, ep: dict) -> UUID:
    """Append decision, prediction and outcome for one routine episode; return the outcome's event id."""
    k = lambda: str(uuid.uuid4())  # noqa: E731
    agent = dict(actor_kind=ActorKind.AGENT, actor_id=_AGENT, source=Source.CHAT, authorship=Authorship.SCOPE_PRINCIPAL)
    d = record_decision(session, key_provider, stream_id=stream_id, idempotency_key=k(), decision_text=ep["decision_text"],
                        **agent).envelope
    p = ep["prediction"]
    pred = record_prediction(session, key_provider, stream_id=stream_id, idempotency_key=k(), decision_id=d.event_id,
                             expected_outcome=p["expected_outcome"], expected_success=p["expected_success"],
                             confidence_pct=p["confidence_pct"], **agent).envelope
    o = ep["outcome"]
    return record_outcome(session, key_provider, stream_id=stream_id, idempotency_key=k(), outcome_for=d.event_id,
                          success=o["success"], evaluates_prediction=pred.event_id,
                          sections=tuple(Section(s["role"], s["text"]) for s in o["sections"]),
                          actor_kind=ActorKind(o["actor_kind"]), actor_id=_REPORTER, source=Source(o["source"]),
                          authorship=_AUTHORSHIP[o["authorship"]]).envelope.event_id


def measure_selectivity(session: ScopedSession, key_provider: RootKeyProvider, stream_id: UUID,
                        episodes: list[dict]) -> SelectivityReport:
    """Load every routine episode into `stream_id`, run the gate once, and report which outcomes it flagged."""
    outcome_to_id = {load_routine_episode(session, key_provider, stream_id, ep): ep["id"] for ep in episodes}
    flagged = sorted(outcome_to_id[f.target_event_id] for f in flag_events(session, key_provider, stream_id)
                     if f.target_event_id in outcome_to_id)
    return SelectivityReport(len(episodes), len(flagged), tuple(flagged))


def item7_passes(recall_complete: bool, report: SelectivityReport, ceiling_permille: int = CEILING_PERMILLE) -> bool:
    """Gate item 7: 100% recall on lesson-bearing episodes AND flag rate <= ceiling on routine episodes."""
    return recall_complete and report.flagged * 1000 <= ceiling_permille * report.episodes
