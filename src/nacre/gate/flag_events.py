"""
Functionality: Flag the events of one stream whose write-gate score reaches the threshold of their learning mode.
Owns: the versioned mode thresholds, scanning a stream's scorable events, idempotent `flag` memory events (one per
  target and threshold version), and serialising concurrent flaggers of a stream.
Public entry: flag_events(), THRESHOLDS, THRESHOLD_VERSION, Flag
Decisions: D-0019, D-0017, D-0003, D-0023
Assumptions: A-0028
Notes: D-0019: the gate only FLAGS; it never promotes. A flag is a `memory_event`, op `flag`, appended in the scored
  event's stream, with caused_by = the target event. Body: target, per-mille score and components, threshold, mode,
  threshold version and reasons.
  Thresholds per mode (per-mille; placeholders tuned in Phase 4, D-0019): normal 500, incident 0 (flag everything),
  exploration 500, onboarding 500. An event's own envelope mode applies (normal when unset).
  D1: idempotency = (target, THRESHOLD_VERSION). Existing flags are found through their caused_by and checked under
  the stream's advisory lock (the same one append_event takes; locks are re-entrant within the transaction), so two
  concurrent flaggers cannot double-flag. D-0012 forbids derived idempotency keys, so the key itself stays random.
"""
import uuid
from dataclasses import dataclass
from uuid import UUID

from nacre.core.event import ActorKind, EventType, Mode, PayloadType, Source
from nacre.core.root_key_provider import RootKeyProvider
from nacre.gate.score_event import SCORABLE, score_event
from nacre.ledger.append_event import AppendRequest, Authorship, append_event
from nacre.ledger.read_stream import read_stream
from nacre.scopes.open_scoped_session import ScopedSession

THRESHOLD_VERSION = "gate-thresholds-v1"
THRESHOLDS = {Mode.NORMAL: 500, Mode.INCIDENT: 0, Mode.EXPLORATION: 500, Mode.ONBOARDING: 500}
GATE_ACTOR = uuid.UUID("a1f0c9de-4c51-4f6e-8a8e-3b1d2f0e9c11")


@dataclass(frozen=True)
class Flag:
    event_id: UUID
    target_event_id: UUID
    score: int


def flag_events(session: ScopedSession, key_provider: RootKeyProvider, stream_id: UUID, *,
                from_seq: int = 1) -> list[Flag]:
    """Score every scorable event of `stream_id` from `from_seq`, and flag those at or above their mode's threshold."""
    session.conn.execute("SELECT pg_advisory_xact_lock(ledger.stream_lock_key(%s))", (stream_id,))
    events = read_stream(session, key_provider, stream_id)
    already = set()
    for e in events:
        c = e.body.get("content") if isinstance(e.body, dict) else None
        if e.envelope.event_type == EventType.MEMORY_EVENT and isinstance(c, dict) and c.get("op") == "flag" \
                and c.get("threshold_version") == THRESHOLD_VERSION:
            already.add(UUID(c["target_event_id"]))
    index = {e.envelope.event_id: e for e in events}
    flags = []
    for e in events:
        env = e.envelope
        if env.commit_seq < from_seq or env.event_type not in SCORABLE or env.event_id in already:
            continue
        s = score_event(session, key_provider, e, index)
        mode = env.mode or Mode.NORMAL
        if s.score < THRESHOLDS[mode]:
            continue
        flag = append_event(session, key_provider, AppendRequest(
            stream_id=stream_id, event_type=EventType.MEMORY_EVENT, payload_type=PayloadType.STRUCTURED,
            actor_kind=ActorKind.SYSTEM, actor_id=GATE_ACTOR, source=Source.SYSTEM,
            authorship=Authorship.SCOPE_PRINCIPAL, idempotency_key=str(uuid.uuid4()), caused_by=env.event_id,
            sources=(env.event_id,),
            content={"op": "flag", "target_event_id": str(env.event_id), "score": s.score, "surprise": s.surprise,
                     "stakes": s.stakes, "statement": s.statement, "threshold": THRESHOLDS[mode], "mode": mode.value,
                     "threshold_version": THRESHOLD_VERSION, "reasons": list(s.reasons)})).envelope
        already.add(env.event_id)
        flags.append(Flag(flag.event_id, env.event_id, s.score))
    return flags
