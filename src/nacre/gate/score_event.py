"""
Functionality: Score one captured event for the write gate: surprise, stakes and direct statements.
Owns: the D-0019 scoring rules (revisions R1 and R4), reading the scored event's linked decision/action/prediction, and
  explaining each component.
Public entry: score_event(), Score, SCORABLE
Decisions: D-0019, D-0018, D-0017
Assumptions: A-0028
Notes: score = max(surprise, stakes, statement), each in PER-MILLE integers 0..1000 (D1: the CBOR subset has no
  floats).
  Surprise (outcome events), D-0019 R1 + R4:
    an AUTHORITATIVE `correction` section (capture/section_authority.py)                         -> 1000, always;
    success = false WITHOUT a valid prediction of exactly this failure                           -> 1000;
    success = false WITH a valid prediction                                                       -> 0;
    success = true where the linked prediction expected failure                                   -> 1000;
    otherwise                                                                                      -> 0.
  A prediction is VALID (R4 anti-gaming) only if: it is linked by `evaluates_prediction`; it responds to the same
  decision; it expected failure; it NAMES the failing check; it was committed BEFORE the first action executing that
  decision (or before the outcome when there is no action); and the outcome's failing_checks are exactly that check
  (trimmed, casefolded). Vague, late, mismatched or extra failures flag. A failing `evaluation` keeps its authority
  (D-0018) but no longer flags by itself (R4 refines R1).
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


def _norm(name: str) -> str:
    return " ".join(name.casefold().split())


def _valid_prediction(pred, outcome_env, content, decision_id, index) -> tuple[bool, str]:
    if pred is None or pred.envelope.event_type != EventType.PREDICTION or not isinstance(pred.body, dict):
        return False, "failure that was not predicted"
    pc = pred.body["content"]
    if {"rel": "response_to", "event_id": str(decision_id)} not in pc.get("refs", []):
        return False, "prediction is about another decision"
    if pc.get("expected_success") is not False:
        return False, "failure where success was predicted"
    check = pc.get("expected_failing_check")
    if not check:
        return False, "vague prediction: no failing check named (R4)"
    actions = [e.envelope.commit_seq for e in index.values() if e.envelope.event_type == EventType.ACTION
               and isinstance(e.body, dict) and {"rel": "execution_of", "event_id": str(decision_id)} in e.body["content"].get("refs", [])]
    boundary = min(actions) if actions else outcome_env.commit_seq
    if pred.envelope.commit_seq >= boundary:
        return False, "prediction recorded after the action (R4)"
    if {_norm(c) for c in content.get("failing_checks", [])} != {_norm(check)}:
        return False, "different or additional failure than predicted (R4)"
    return True, "failure predicted by name, before the action"


def score_event(session: ScopedSession, key_provider: RootKeyProvider, event: ReadEvent,
                index: dict[UUID, ReadEvent] | None = None) -> Score:
    """Score `event` (read with its decrypted body). `index` (event_id -> ReadEvent of the same stream) avoids
    re-reading the stream when scoring many events. Unscorable or shredded events score 0."""
    env, reasons = event.envelope, []
    content = event.body.get("content") if isinstance(event.body, dict) else None
    surprise = stakes = statement = 0
    if index is None and env.event_type == EventType.OUTCOME:
        index = {e.envelope.event_id: e for e in read_stream(session, key_provider, env.stream_id)}
    if env.event_type in SCORABLE and isinstance(content, dict | str):
        if env.event_type in (EventType.STATEMENT, EventType.CORRECTION) and env.trust == Trust.TRUSTED \
                and env.actor_kind == ActorKind.PERSON:
            statement = FULL
            reasons.append("trusted direct statement or correction by a person")
        if env.event_type == EventType.OUTCOME and isinstance(content, dict):
            success = content.get("success")
            refs = {r["rel"]: UUID(r["event_id"]) for r in content.get("refs", [])}
            linked = {i: index[i] for i in refs.values() if i in index}
            pred = linked.get(refs.get("evaluates_prediction"))
            if any(s["role"] == "correction" and section_authority(env, "correction", success).authoritative
                   for s in content.get("sections", [])):
                surprise = FULL
                reasons.append("authoritative correction (R1)")
            elif success is False:
                target_id = refs.get("outcome_for")
                target_ev = index.get(target_id)
                if target_ev is not None and target_ev.envelope.event_type == EventType.ACTION and isinstance(target_ev.body, dict):
                    target_id = next((UUID(r["event_id"]) for r in target_ev.body["content"]["refs"]
                                      if r["rel"] == "execution_of"), target_id)
                ok, why = _valid_prediction(pred, env, content, target_id, index)
                surprise = 0 if ok else FULL
                reasons.append(why)
            elif success is True and pred is not None and isinstance(pred.body, dict) \
                    and pred.body["content"].get("expected_success") is False:
                surprise = FULL
                reasons.append("success where failure was predicted")
            target = linked.get(refs.get("outcome_for"))
            target_tags = target.body["content"].get("stakes", []) if target and isinstance(target.body, dict) else []
            if content.get("stakes") or target_tags:
                stakes = FULL
                reasons.append("stakes: " + ",".join(sorted(set(content.get("stakes", [])) | set(target_tags))))
    return Score(env.event_id, max(surprise, stakes, statement), surprise, stakes, statement, tuple(reasons))
