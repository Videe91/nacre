"""
Functionality: Record a contradiction proposal pinned to one exact belief version.
Owns: resolving the belief's current head (version and event), checking the contradicting decision has at least one
  real outcome, and the `contradiction_proposed` memory event.
Public entry: propose_contradiction(), ContradictionError
Decisions: D-0017, D-0020
Assumptions: none
Notes: Proposal only (authority "proposal_only"); it never changes the belief (MNEXA ledger 38). Pinned to the head
  version seen NOW, so a later version is not contested by stale evidence (contest considers only proposals pinned to
  the current head). In Phase 2 the trigger is a trusted correction of a belief-version event (D-0020); automatic
  detection needs Phase 3 recall.
"""
import uuid
from uuid import UUID

from nacre.core.event import ActorKind, EventType, PayloadType, Source
from nacre.core.root_key_provider import RootKeyProvider
from nacre.ledger.append_event import AppendRequest, Authorship, append_event
from nacre.ledger.read_stream import read_stream
from nacre.scopes.open_scoped_session import ScopedSession
from nacre.stores.write_version import VERSION_ACTOR, normalize, read_version_events


class ContradictionError(ValueError):
    """The contradiction has no real ancestry or no belief to target."""


def propose_contradiction(session: ScopedSession, key_provider: RootKeyProvider, *, stream_id: UUID,
                          belief_object_id: UUID, decision_id: UUID, text: str, run_id: UUID | None = None):
    """Append a `contradiction_proposed` event against the belief's current head; return its envelope."""
    if not isinstance(text, str) or not text.strip():
        raise ContradictionError("empty contradiction")
    heads = [v for v in read_version_events(session, key_provider, stream_id)
             if v.body["content"]["object_id"] == str(belief_object_id) and v.body["content"]["kind"] == "belief"]
    if not heads:
        raise ContradictionError("no belief with that id in this stream")
    head = heads[-1]
    events = read_stream(session, key_provider, stream_id)
    decision = [e for e in events if e.envelope.event_id == decision_id and e.envelope.event_type == EventType.DECISION]
    outcomes = [e.envelope.event_id for e in events if e.envelope.event_type == EventType.OUTCOME and isinstance(e.body, dict)
                and {"rel": "outcome_for", "event_id": str(decision_id)} in e.body["content"].get("refs", [])]
    if not decision or not outcomes:
        raise ContradictionError("the contradicting decision needs at least one observed outcome")
    content = {"op": "contradiction_proposed", "authority": "proposal_only", "target_object_id": str(belief_object_id),
               "target_version": head.body["content"]["version"], "target_event_id": str(head.envelope.event_id),
               "decision_id": str(decision_id), "outcome_ids": [str(o) for o in outcomes], "text": text,
               "key": normalize(text), "run_id": str(run_id) if run_id else None}
    return append_event(session, key_provider, AppendRequest(
        stream_id=stream_id, event_type=EventType.MEMORY_EVENT, payload_type=PayloadType.STRUCTURED,
        actor_kind=ActorKind.SYSTEM, actor_id=VERSION_ACTOR, source=Source.SYSTEM,
        authorship=Authorship.SCOPE_PRINCIPAL, idempotency_key=str(uuid.uuid4()), content=content,
        caused_by=head.envelope.event_id, cycle_id=run_id)).envelope
