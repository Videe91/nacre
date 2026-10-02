"""
Functionality: Append one day of one EXP-0004 scope's history through the capture functions, in order, mapping every
  dataset event to its D-0018 capture call.
Owns: the per-run id map (dataset event ids -> ledger event ids; dataset authors, principals and scopes -> UUIDs),
  the event-type -> capture-function mapping, the actor / source / authorship mapping, and refusing any event shape
  the mapping does not know.
Public entry: capture_day(), IdMap, CaptureMappingError
Decisions: D-0018, D-0012, D-0016, D-0023
Assumptions: A-0026, A-0038
Notes: EVALUATION HARNESS ONLY: the one place where EXP-0004 history becomes capture input (as load_mnexa_family.py
  for EXP-0003). Only the arm view (load_exp0004_set.ArmScope) is passed here; grading fields never are.
  - Ids: every stream, principal and person UUID is uuid5 of the run id, so each replicate (new run id) has fresh
    ones and the same run id always maps the same way (D1). Idempotency keys are caller-random UUID v4 (D-0012).
  - Actors: actor_kind agent / person / system as given; actor_id = the author's UUID. Person-authored events are
    therefore under that person's key (D-0023), which is what erase_person(P) destroys.
  - Bodies: decision -> record_decision(decision_text, decision_kind); prediction -> record_prediction
    (confidence 0..1 -> confidence_pct = round(100 x c), D1: the CBOR subset has no floats); action -> record_action;
    outcome -> record_outcome(sections, success, failing_checks, evaluates_prediction). `reasoning_owner`, `dispatched`
    and `decided_from = null` are the capture defaults and are checked, not passed.
  - Event `addresses` are NOT captured: the capture functions do not accept them yet (the D-0018 amendment of D-0025 §3
    is not built). They are counted in `IdMap.dropped_addresses` and reported (gap for the owner).
  - Trust is derived by append_event from source + authorship (D-0012); a dataset `trust` that disagrees with the
    derived one is refused (it would mean the mapping is wrong).
"""
import uuid
from dataclasses import dataclass, field
from uuid import UUID

from nacre.capture.record_action import record_action
from nacre.capture.record_decision import record_decision
from nacre.capture.record_outcome import Section, record_outcome
from nacre.capture.record_prediction import record_prediction
from nacre.core.event import ActorKind, Source
from nacre.core.root_key_provider import RootKeyProvider
from nacre.ledger.append_event import Authorship
from nacre.scopes.open_scoped_session import ScopedSession


class CaptureMappingError(ValueError):
    """A dataset event does not fit the pre-registered capture mapping (no event text in the message)."""


@dataclass
class IdMap:
    run_id: UUID
    events: dict[str, UUID] = field(default_factory=dict)
    dropped_addresses: int = 0

    def actor(self, name: str) -> UUID:
        return uuid.uuid5(self.run_id, f"actor|{name}")

    def stream(self, scope_id: str) -> UUID:
        return uuid.uuid5(self.run_id, f"scope|{scope_id}")


def _ref(ev: dict, rel: str, ids: IdMap, required: bool = True) -> UUID | None:
    hits = [r["event_id"] for r in ev["refs"] if r["rel"] == rel]
    if len(hits) > 1 or (required and not hits):
        raise CaptureMappingError(f"{ev['event_id']}: expected exactly one {rel} ref")
    if not hits:
        return None
    if hits[0] not in ids.events:
        raise CaptureMappingError(f"{ev['event_id']}: {rel} names an event not captured before it")
    return ids.events[hits[0]]


def _append(s: ScopedSession, kp: RootKeyProvider, stream: UUID, ev: dict, ids: IdMap, cycle_id: UUID | None):
    b, t = ev["body"], ev["event_type"]
    allowed_rels = {"decision": set(), "prediction": {"response_to"}, "action": {"execution_of"},
                    "outcome": {"outcome_for", "evaluates_prediction"}}.get(t)
    if allowed_rels is None or {r["rel"] for r in ev["refs"]} - allowed_rels:
        raise CaptureMappingError(f"{ev['event_id']}: unmapped event type or reference")
    who = dict(stream_id=stream, actor_kind=ActorKind(ev["actor_kind"]), actor_id=ids.actor(ev["author"]),
               source=Source(ev["source"]), authorship=Authorship(ev["authorship"]),
               idempotency_key=str(uuid.uuid4()), cycle_id=cycle_id)
    if t == "decision":
        if b.get("decided_from") is not None or b.get("reasoning_owner", "external") != "external":
            raise CaptureMappingError(f"{ev['event_id']}: decided_from / reasoning_owner not mappable")
        return record_decision(s, kp, decision_text=b["decision_text"], decision_kind=b["decision_kind"], **who)
    if t == "prediction":
        conf = b.get("confidence")
        return record_prediction(s, kp, decision_id=_ref(ev, "response_to", ids), expected_outcome=b["expected_outcome"],
                                 expected_success=b.get("expected_success"), predictor=b.get("predictor", "agent"),
                                 confidence_pct=None if conf is None else round(100 * conf),
                                 expected_failing_check=b.get("expected_failing_check"), **who)
    if t == "action":
        if b.get("dispatched", True) is not True:
            raise CaptureMappingError(f"{ev['event_id']}: an undispatched action is not mappable")
        return record_action(s, kp, decision_id=_ref(ev, "execution_of", ids), action_kind=b["action_kind"],
                             description=b["description"], **who)
    return record_outcome(s, kp, outcome_for=_ref(ev, "outcome_for", ids), success=b.get("success"),
                          sections=tuple(Section(x["role"], x["text"]) for x in b["sections"]),
                          evaluates_prediction=_ref(ev, "evaluates_prediction", ids, required=False),
                          failing_checks=tuple(b.get("failing_checks") or ()), **who)


def capture_day(session: ScopedSession, key_provider: RootKeyProvider, stream_id: UUID,
                episodes: tuple[tuple[dict, ...], ...], ids: IdMap, *, cycle_id: UUID | None = None) -> list[UUID]:
    """Append every event of the day's episodes in order; returns the ledger event ids (also recorded in `ids`)."""
    out = []
    for episode in episodes:
        for ev in episode:
            if ev["event_id"] in ids.events:
                raise CaptureMappingError(f"{ev['event_id']}: captured twice")
            env = _append(session, key_provider, stream_id, ev, ids, cycle_id).envelope
            if ev.get("trust") is not None and env.trust.value != ev["trust"]:
                raise CaptureMappingError(f"{ev['event_id']}: derived trust differs from the set's")
            ids.events[ev["event_id"]] = env.event_id
            ids.dropped_addresses += len(ev.get("addresses") or ())
            out.append(env.event_id)
    return out
