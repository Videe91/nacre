"""
Functionality: Run one sleep pass over a stream: flag, then consolidate every flagged, unprocessed episode into
  grounded proposals, promotions and an episode, resumably.
Owns: the run markers, choosing the episodes, the per-step transactions, reusing recorded model calls on resume,
  choosing the final proposal list, and the run report.
Public entry: run_sleep_pass(), SleepReport, EPISODE_DONE, PASS_COMPLETED, REFUSED
Decisions: D-0020, D-0018, D-0019, D-0017, D-0021, D-0022, D-0023
Assumptions: A-0025, A-0026, A-0027
Notes: Transactions (D-0020 idempotency and crash safety):
    1. `sleep_pass_started` marker + gate flags (D-0019);
    2. per flagged outcome, each model call in its OWN committed session, so paid calls are never lost;
    3. per episode, ONE transaction: admission -> lesson proposals -> promotions -> episode (runtime_rule) ->
       `sleep_episode_done` marker. A crash leaves the episode either fully done or not at all;
    4. `sleep_pass_completed` marker.
  Resume: episodes with a `sleep_episode_done` marker (any run) are skipped. Before a live call, an existing
  recording of the same request in the stream is replayed instead (models/recorded_provider.py), so a re-run neither
  pays twice nor appends twice. Identical evidence in one stream therefore gets the same answer (D1).
  Final list: the repair's list when it parsed, else the proposer's (the fallback still guards structure).
  D-0023: every episode write is keyed by its sources. When that is impossible (a shredded source, or more than 256
  contributors) the episode is REFUSED and a content-free `derived_write_refused` marker is recorded in its own
  committed session; there is never a stream-key fallback, and the episode is not retried.
  D-0020 amendment 1: an outcome recorded against an ACTION is consolidated through the action's recorded decision
  link (sleep/build_evidence_bundle.py); the episode's members are (decision, action, outcome) in that order. An
  action with no readable decision link is not consolidated and is counted in `unlinked_action_outcomes`, never in
  `skipped`; nothing is written for it (no marker), so every run reports it again (D1).
  Only OUTCOME flags are consolidated in Phase 2. Corrections of belief versions -> contradictions is not built yet
  (tracked in CURRENT.md). Injected or task data never enters here (gate items 11-12): the only inputs are the
  stream's own events.
"""
import uuid
from collections import Counter
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID

from nacre.core.event import ActorKind, EventType, PayloadType, Source
from nacre.core.model_provider import CallPolicy, ModelProvider
from nacre.core.root_key_provider import RootKeyProvider
from nacre.gate.flag_events import flag_events
from nacre.ledger.append_event import AppendRequest, Authorship, append_event
from nacre.ledger.read_stream import read_stream
from nacre.keys.derive_contributor_key import ContributorError
from nacre.ledger.validate_append import AppendError
from nacre.models.call_model import DEFAULT_POLICY, ModelCallRefused
from nacre.models.recorded_provider import RecordedProvider, RecordingMiss
from nacre.scopes.open_scoped_session import ScopedSession
from nacre.sleep.admit_propositions import admit_propositions
from nacre.sleep.build_evidence_bundle import BundleError, UnlinkedAction, build_evidence_bundle
from nacre.sleep.propose_propositions import propose_propositions
from nacre.sleep.repair_structure import repair_structure
from nacre.stores.commit_episode import SLEEP_PASS_STARTED, Anchor, commit_episode
from nacre.stores.promote_if_supported import promote_if_supported
from nacre.stores.propose_lesson import Qualifier, propose_lesson
from nacre.stores.write_version import VERSION_ACTOR

EPISODE_DONE = "sleep_episode_done"
REFUSED = "derived_write_refused"
PASS_COMPLETED = "sleep_pass_completed"


@dataclass
class SleepReport:
    run_id: UUID
    episodes: int = 0
    skipped: int = 0
    unlinked_action_outcomes: int = 0
    structured: int = 0
    fallback: int = 0
    rejections: Counter = field(default_factory=Counter)
    parse_errors: int = 0
    calls_live: int = 0
    calls_reused: int = 0
    cost_usd: Decimal = Decimal(0)
    promoted: int = 0
    refused: int = 0


def _marker(s, kp, stream, content, run_id, sources=()):
    append_event(s, kp, AppendRequest(
        stream_id=stream, event_type=EventType.MEMORY_EVENT, payload_type=PayloadType.STRUCTURED,
        actor_kind=ActorKind.SYSTEM, actor_id=VERSION_ACTOR, source=Source.SYSTEM, authorship=Authorship.SCOPE_PRINCIPAL,
        idempotency_key=str(uuid.uuid4()), content=content, cycle_id=run_id, sources=tuple(sources)))


def _refused(exc: Exception) -> bool:
    """A D-0023 refusal: the derived write could not be keyed (shredded source or over the contributor cap)."""
    return isinstance(exc, ModelCallRefused) and "D-0023" in str(exc) or isinstance(exc, AppendError) \
        and isinstance(exc.__cause__, ContributorError)


def _seat(open_session, kp, live, stream, report, call):
    """Run one seat: replay an existing recording of the same request, else call live (committed on its own)."""
    with open_session() as s:
        try:
            out = call(s, RecordedProvider(s, kp, [stream]))
            report.calls_reused += out is not None          # None: the seat had nothing to do, no call either way
            return out
        except RecordingMiss:
            pass
    with open_session() as s:
        out = call(s, live)
    if out is not None:
        report.calls_live += 1
        report.cost_usd += out.call.cost_usd
    return out


def run_sleep_pass(open_session: Callable[[], AbstractContextManager[ScopedSession]], key_provider: RootKeyProvider,
                   model_provider: ModelProvider, stream_id: UUID, *, policy: CallPolicy = DEFAULT_POLICY,
                   run_id: UUID | None = None) -> SleepReport:
    """Consolidate every flagged, unprocessed outcome episode of `stream_id`."""
    run_id = run_id or uuid.uuid4()
    report = SleepReport(run_id)
    with open_session() as s:
        _marker(s, key_provider, stream_id, {"op": SLEEP_PASS_STARTED, "run_id": str(run_id)}, run_id)
        flag_events(s, key_provider, stream_id)
    with open_session() as s:
        events = read_stream(s, key_provider, stream_id)
    index = {e.envelope.event_id: e for e in events}
    contents = [e.body["content"] for e in events if isinstance(e.body, dict) and isinstance(e.body.get("content"), dict)]
    done = {c["outcome_id"] for c in contents if c.get("op") in (EPISODE_DONE, REFUSED)}
    todo = [UUID(c["target_event_id"]) for c in contents if c.get("op") == "flag" and c["target_event_id"] not in done
            and index.get(UUID(c["target_event_id"])) is not None
            and index[UUID(c["target_event_id"])].envelope.event_type == EventType.OUTCOME]
    for outcome_id in dict.fromkeys(todo):
        try:
            bundle = build_evidence_bundle(None, key_provider, stream_id, outcome_id, index)
        except UnlinkedAction:
            report.unlinked_action_outcomes += 1          # D-0020 am.1: counted, never inferred
            continue
        except BundleError:
            report.skipped += 1
            continue
        try:
            _consolidate(open_session, key_provider, model_provider, stream_id, index, bundle, outcome_id, run_id,
                         policy, report)
        except Exception as exc:
            if not _refused(exc):
                raise
            with open_session() as s:                # D-0023: refused and RECORDED (content-free, outside the failed tx)
                _marker(s, key_provider, stream_id, {"op": REFUSED, "run_id": str(run_id), "outcome_id": str(outcome_id),
                                                     "reason": type(exc.__cause__ or exc).__name__}, run_id)
            report.refused += 1
    with open_session() as s:
        head = max((e.envelope.commit_seq for e in read_stream(s, key_provider, stream_id)), default=0)
        _marker(s, key_provider, stream_id, {"op": PASS_COMPLETED, "run_id": str(run_id), "through_seq": head}, run_id)
    return report


def _consolidate(open_session, key_provider, model_provider, stream_id, index, bundle, outcome_id, run_id, policy, report):
    """One episode: the two seats (each committed alone), then one atomic transaction for everything it writes."""
    first = _seat(open_session, key_provider, model_provider, stream_id, report,
                  lambda s, p: propose_propositions(s, key_provider, p, bundle, run_id=run_id, policy=policy))
    repaired = _seat(open_session, key_provider, model_provider, stream_id, report,
                     lambda s, p: repair_structure(s, key_provider, p, bundle, first.proposals, run_id=run_id, policy=policy))
    report.parse_errors += bool(first.parse_error) + bool(repaired is not None and repaired.parse_error)
    final = repaired.proposals if repaired is not None and repaired.parse_error is None else first.proposals
    with open_session() as s:
        admission = admit_propositions(bundle, final)
        report.rejections.update(reason for _, reason in admission.rejections)
        for adm in admission.structured + admission.fallback:
            pid = propose_lesson(s, key_provider, stream_id=stream_id, decision_id=bundle.decision_id,
                                 outcome_id=outcome_id, section_index=adm.section, span=adm.span, nucleus=adm.nucleus,
                                 qualifiers=tuple(Qualifier(t, x) for t, x in adm.qualifiers), run_id=run_id).event_id
            if promote_if_supported(s, key_provider, stream_id, pid, cycle_id=run_id) is not None:
                report.promoted += 1
        d = index[bundle.decision_id].envelope
        anchors = tuple(Anchor(b, getattr(d, b)) for b in ("task_id", "cycle_id") if getattr(d, b) is not None)
        members = (bundle.decision_id, *((bundle.action_id,) if bundle.action_id else ()), outcome_id)
        commit_episode(s, key_provider, stream_id=stream_id, members=members, anchors=anchors,
                       boundary_method="runtime_rule", run_id=run_id)
        _marker(s, key_provider, stream_id, {"op": EPISODE_DONE, "run_id": str(run_id), "outcome_id": str(outcome_id),
                                             "structured": len(admission.structured), "fallback": len(admission.fallback),
                                             "rejected": len(admission.rejections)}, run_id, sources=(outcome_id,))
    report.episodes += 1
    report.structured += len(admission.structured)
    report.fallback += len(admission.fallback)
