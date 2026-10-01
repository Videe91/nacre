"""
Functionality: Validate the typed references of a capture event before it is appended.
Owns: the reference vocabulary (MNEXA ADR-0008, as adopted by D-0018), which event types each relation may point
  to, and the checks that every target is an already-committed event of the same stream, readable in the session.
Public entry: validate_refs(), Ref, RefError, RELATIONS
Decisions: D-0018, D-0002
Assumptions: none
Notes: References are structural, never causal claims: `caused_by`, `explains`, `proves` and the like are not in the
  vocabulary (MNEXA ADR-0008). They point backward only and stay within one stream. A new event cannot reference
  itself because its id does not exist yet. Duplicate (rel, target) pairs are refused, so a reference list is a set
  in a stable order.
"""
from dataclasses import dataclass
from uuid import UUID

from nacre.core.event import EventType
from nacre.scopes.open_scoped_session import ScopedSession

_E = EventType
RELATIONS: dict[str, frozenset[EventType]] = {
    "outcome_for": frozenset({_E.DECISION, _E.ACTION}),
    "execution_of": frozenset({_E.DECISION}),
    "response_to": frozenset({_E.DECISION, _E.MESSAGE, _E.STATEMENT}),
    "correction_of": frozenset(set(EventType) - {_E.DELETION_MARKER}),
    "evaluates_prediction": frozenset({_E.PREDICTION}),
    "continuation_of": frozenset({_E.DECISION, _E.ACTION, _E.OUTCOME, _E.MESSAGE}),
}


@dataclass(frozen=True)
class Ref:
    rel: str
    event_id: UUID


class RefError(ValueError):
    """A reference is not allowed."""


def validate_refs(session: ScopedSession, stream_id: UUID, refs: list[Ref]) -> list[dict]:
    """Check `refs` against committed events of `stream_id`; return them as body dicts, in the given order."""
    seen = set()
    for r in refs:
        if not isinstance(r, Ref) or r.rel not in RELATIONS or type(r.event_id) is not UUID:
            raise RefError(f"not an allowed reference: {r!r}")
        if (r.rel, r.event_id) in seen:
            raise RefError(f"duplicate reference {r.rel} -> {r.event_id}")
        seen.add((r.rel, r.event_id))
    if refs:
        found = dict(session.conn.execute(
            "SELECT event_id, event_type FROM ledger.events WHERE stream_id = %s AND event_id = ANY(%s)",
            (stream_id, [r.event_id for r in refs])).fetchall())
        for r in refs:
            if r.event_id not in found:
                raise RefError(f"{r.rel} target {r.event_id} is not a committed, readable event of this stream")
            if EventType(found[r.event_id]) not in RELATIONS[r.rel]:
                raise RefError(f"{r.rel} cannot point to a {found[r.event_id]} event")
    return [{"rel": r.rel, "event_id": str(r.event_id)} for r in refs]
