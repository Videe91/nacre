"""
Functionality: Score one captured event for the write gate: surprise, stakes and direct statements.
Owns: the D-0019 scoring rules (with revision R1), reading the scored event's linked decision/action/prediction, and
  explaining each component.
Public entry: score_event(), Score, SCORABLE
Decisions: D-0019, D-0018, D-0017
Assumptions: A-0028
Notes: score = max(surprise, stakes, statement), each in PER-MILLE integers 0..1000 (D1: the CBOR subset has no
  floats).
  Surprise (outcome events):
    R1 - any AUTHORITATIVE correction / failing-evaluation section (capture/section_authority.py)  -> 1000, whatever
         the prediction said;
    success = false with no `evaluates_prediction`, or a prediction that expected success          -> 1000;
    success = false and the linked prediction expected failure                                       -> 0;
    success = true and the linked prediction expected failure                                        -> 1000;
    otherwise                                                                                         -> 0.
  Stakes: a stakes tag on the outcome or on the decision/action it is for -> 1000. The D-0019 config keyword rules
    are not built yet (tracked in CURRENT.md); tags only.
  Statement: a TRUSTED `statement` or `correction` event whose actor is a person -> 1000.
  Deterministic, no model call, and it never reads free text for meaning (only section roles and authority).
"""
from dataclasses import dataclass
from uuid import UUID

from nacre.capture.section_authority import section_authority
from nacre.core.event import ActorKind, EventType, Trust
from nacre.core.root_key_provider import RootKeyProvider
from nacre.ledger.read_stream import ReadEvent, read_stream
from nacre.scopes.open_scoped_session import ScopedSession

SCORABLE = frozenset({EventType.OUTCOME, EventType.STATEMENT, EventType.CORRECTION})
FULL = 1000


@dataclass(frozen=True)
class Score:
    target_event_id: UUID
    score: int
    surprise: int
    stakes: int
    statement: int
    reasons: tuple[str, ...]


def _by_id(session, key_provider, stream_id, ids, index):
    if not ids:
        return {}
    if index is None:
        index = {e.envelope.event_id: e for e in read_stream(session, key_provider, stream_id)}
    return {i: index[i] for i in ids if i in index}


def score_event(session: ScopedSession, key_provider: RootKeyProvider, event: ReadEvent,
                index: dict[UUID, ReadEvent] | None = None) -> Score:
    """Score `event` (read with its decrypted body). `index` (event_id -> ReadEvent of the same stream) avoids
    re-reading the stream when scoring many events. Unscorable or shredded events score 0."""
    env, reasons = event.envelope, []
    content = event.body.get("content") if isinstance(event.body, dict) else None
    surprise = stakes = statement = 0
    if env.event_type in SCORABLE and isinstance(content, dict | str):
        if env.event_type in (EventType.STATEMENT, EventType.CORRECTION) and env.trust == Trust.TRUSTED \
                and env.actor_kind == ActorKind.PERSON:
            statement = FULL
            reasons.append("trusted direct statement or correction by a person")
        if env.event_type == EventType.OUTCOME and isinstance(content, dict):
            success = content.get("success")
            refs = {r["rel"]: UUID(r["event_id"]) for r in content.get("refs", [])}
            linked = _by_id(session, key_provider, env.stream_id, set(refs.values()), index)
            if any(section_authority(env, s["role"], success).authoritative for s in content.get("sections", [])):
                surprise = FULL
                reasons.append("authoritative correction (R1)")
            else:
                pred = linked.get(refs.get("evaluates_prediction"))
                expected = pred.body["content"].get("expected_success") if pred and isinstance(pred.body, dict) else None
                if success is False and expected is not False:
                    surprise = FULL
                    reasons.append("failure that was not predicted")
                elif success is True and expected is False:
                    surprise = FULL
                    reasons.append("success where failure was predicted")
            target = linked.get(refs.get("outcome_for"))
            target_tags = target.body["content"].get("stakes", []) if target and isinstance(target.body, dict) else []
            if content.get("stakes") or target_tags:
                stakes = FULL
                reasons.append("stakes: " + ",".join(sorted(set(content.get("stakes", [])) | set(target_tags))))
    return Score(env.event_id, max(surprise, stakes, statement), surprise, stakes, statement, tuple(reasons))
