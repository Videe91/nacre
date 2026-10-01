"""
Functionality: Validate an episode proposal for admissibility and commit it as an episode version.
Owns: the MNEXA ADR-0009 rules adopted by D-0017: opaque identity, explicit lineage targeting, ordered membership as
  the version's edge set, identifier-based anchors that must match their members, consolidation-only formation,
  runtime-only commits, and boundary provenance.
Public entry: commit_episode(), Anchor, EpisodeError, ANCHOR_BASES, BOUNDARY_METHODS, SLEEP_PASS_STARTED
Decisions: D-0017, D-0020
Assumptions: none
Notes: Invariants (MNEXA ADR-0009 Q-n, each tested in tests/stores/test_commit_episode.py):
    Q-1  members resolve to committed events of the stream;      Q-2/3 membership+order are the version's edges, new
    Q-4  identity is uuid4, issued here, never from keys/members;      versions never change old ones (append-only);
    Q-5  every anchor matches at least one member's envelope;    Q-6  anchor bases are identifiers only (no time gaps);
    Q-7  the runtime commits (actor system); model-proposed boundaries must cite the model's `result` event;
    Q-8  boundary method is recorded, and the model identity when model-proposed;
    Q-9  episodes form only inside a sleep pass: run_id must have a `sleep_pass_started` memory event in the stream.
         (MNEXA's "never inside an evaluation epoch" has no Phase 2 counterpart: Nacre has no evaluation epochs yet.
         Recorded in CURRENT.md.)
    Q-10 membership may span several anchors;                    Q-11 no exclusivity: a record may join many episodes;
    Q-12 a version > 1 only when the proposal names the lineage (target_object_id); otherwise a NEW object;
    Q-13 the checks are admissibility only;                      Q-14 the version is an interpretive claim, not truth;
    Q-15 several episodes may share a session, and one may span several sessions.
  D1: the `sleep_pass_started` marker op is defined here; sleep/run_sleep_pass.py writes it.
"""
import uuid
from dataclasses import dataclass
from uuid import UUID

from nacre.core.event import ActorKind, EventType
from nacre.core.root_key_provider import RootKeyProvider
from nacre.ledger.read_stream import read_stream
from nacre.scopes.open_scoped_session import ScopedSession
from nacre.stores.write_version import Edge, VersionRecord, read_version_events, write_version

ANCHOR_BASES = frozenset({"task_id", "cycle_id"})
BOUNDARY_METHODS = frozenset({"runtime_rule", "model_proposed"})
SLEEP_PASS_STARTED = "sleep_pass_started"


class EpisodeError(ValueError):
    """The episode proposal is not admissible."""


@dataclass(frozen=True)
class Anchor:
    basis: str
    value: UUID


def commit_episode(session: ScopedSession, key_provider: RootKeyProvider, *, stream_id: UUID, members: tuple[UUID, ...],
                   anchors: tuple[Anchor, ...], boundary_method: str, run_id: UUID,
                   model_call_event_id: UUID | None = None, target_object_id: UUID | None = None) -> tuple[UUID, int]:
    """Commit one episode version; return (object_id, version)."""
    if not members or len(set(members)) != len(members):
        raise EpisodeError("an episode needs distinct members")
    if boundary_method not in BOUNDARY_METHODS:
        raise EpisodeError(f"boundary_method must be one of {sorted(BOUNDARY_METHODS)}")
    events = {e.envelope.event_id: e for e in read_stream(session, key_provider, stream_id)}
    if not any(isinstance(e.body, dict) and isinstance(e.body.get("content"), dict)
               and e.body["content"].get("op") == SLEEP_PASS_STARTED and e.body["content"].get("run_id") == str(run_id)
               for e in events.values()):
        raise EpisodeError("episodes form only inside a sleep pass (Q-9): unknown run_id")
    missing = [m for m in members if m not in events]
    if missing:
        raise EpisodeError(f"members must be committed events of this stream (Q-1): {missing[0]}")
    for a in anchors:
        if a.basis not in ANCHOR_BASES:
            raise EpisodeError(f"anchors are identifiers only (Q-6), not {a.basis!r}")
        if not any(getattr(events[m].envelope, a.basis) == a.value for m in members):
            raise EpisodeError(f"anchor {a.basis}={a.value} matches no member (Q-5)")
    model = None
    if boundary_method == "model_proposed":
        call = events.get(model_call_event_id)
        if call is None or call.envelope.event_type != EventType.RESULT or call.envelope.actor_kind != ActorKind.MODEL:
            raise EpisodeError("a model-proposed boundary must cite the model's recorded result event (Q-7, Q-8)")
        model = call.envelope.actor_model
    if target_object_id is None:
        object_id, version = uuid.uuid4(), 1
    else:
        prior = [v for v in read_version_events(session, key_provider, stream_id)
                 if v.body["content"]["object_id"] == str(target_object_id) and v.body["content"]["kind"] == "episode"]
        if not prior:
            raise EpisodeError("target_object_id names no episode in this stream (Q-12)")
        object_id, version = target_object_id, prior[-1].body["content"]["version"] + 1
    content = {"members": [str(m) for m in members], "anchors": [{"basis": a.basis, "value": str(a.value)} for a in anchors],
               "boundary_method": boundary_method, "model": model,
               "model_call_event_id": str(model_call_event_id) if model_call_event_id else None,
               "run_id": str(run_id), "lineage_target": str(target_object_id) if target_object_id else None,
               "epistemic_status": "interpretive_claim"}
    edges = tuple(Edge("member", target_event_id=m) for m in members)
    if model_call_event_id:
        edges += (Edge("derived_from", target_event_id=model_call_event_id),)
    write_version(session, key_provider, stream_id, VersionRecord(object_id, version, "episode", "active", None, content, edges),
                  cycle_id=run_id)
    return object_id, version
