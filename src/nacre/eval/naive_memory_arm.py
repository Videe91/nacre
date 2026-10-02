"""
Functionality: EXP-0004 arm V (naive memory): embed the text of every readable captured event in the principal's
  granted scopes, and paste the top-10 by cosine to the task prompt, in rank order.
Owns: which events count as captured, the text of one event, the index over one principal's granted scopes, the
  cosine top-k with its tie order, and the rendered memory section.
Public entry: build_naive_index(), NaiveIndex, NaiveItem, event_text(), render_naive_section(), CAPTURE_TYPES, TOP_K
Decisions: D-0025, D-0024, D-0005, D-0023, D-0016
Assumptions: A-0034
Notes: EVALUATION HARNESS ONLY (EXP-0004 "Arms", row V; R26). V is deliberately generous: it respects grants and
  erasure BY CONSTRUCTION, because it reads only through the caller's scoped session (RLS, D-0005; read_stream refuses
  an ungranted stream) and drops every event whose body is Shredded (its key was destroyed, D-0023).
  - Captured events: event types decision, prediction, action, outcome, correction (the D-0018 capture shapes), from
    every source, trusted or not. Model-call results, memory events (beliefs, episodes, traces), config events and
    deletion markers are not captured events and are never included.
  - Event text (D1, flagged to the owner as an open point of the V definition): the event's text fields in body
    order, joined by a single space, with no role, source or trust labels: decision_text; expected_outcome and
    expected_failing_check; description; every section's text, then failing_checks; a correction's text.
  - Embedder: injected (core.embedder.Embedder); the runner passes the same embedder recall uses
    (index_version.default_embedder()). Vectors are unit-normalised, so cosine = dot product.
  - Rank: cosine descending; ties by stream id, then commit_seq (deterministic). Top-10 (= N's 10-item budget).
  - Section: "<n>. <text>" per item in rank order, newline-separated; "(none)" when nothing is readable. V has no
    character budget (the EXP-0004 V row sets none).
"""
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

import numpy as np

from nacre.core.embedder import Embedder
from nacre.core.event import EventType
from nacre.core.root_key_provider import RootKeyProvider
from nacre.ledger.read_stream import read_stream
from nacre.scopes.open_scoped_session import ScopedSession

TOP_K = 10
CAPTURE_TYPES = frozenset({EventType.DECISION, EventType.PREDICTION, EventType.ACTION, EventType.OUTCOME,
                           EventType.CORRECTION})


@dataclass(frozen=True)
class NaiveItem:
    event_id: UUID
    stream_id: UUID
    commit_seq: int
    text: str
    score: float = 0.0


def event_text(event_type: EventType, content: dict) -> str:
    """The pasted (and embedded) text of one captured event."""
    keys = {EventType.DECISION: ("decision_text",), EventType.PREDICTION: ("expected_outcome", "expected_failing_check"),
            EventType.ACTION: ("description",), EventType.CORRECTION: ("text",)}.get(event_type, ())
    parts = [content.get(k) for k in keys]
    if event_type == EventType.OUTCOME:
        parts = [s.get("text") for s in content.get("sections") or []] + list(content.get("failing_checks") or [])
    return " ".join(p.strip() for p in parts if isinstance(p, str) and p.strip())


class NaiveIndex:
    def __init__(self, items: Sequence[NaiveItem], vectors: np.ndarray, embedder: Embedder):
        self.items, self._vectors, self._embedder = tuple(items), vectors, embedder

    def top(self, query: str, k: int = TOP_K) -> list[NaiveItem]:
        """The k items most similar to `query`, in rank order (cosine desc, then stream id, then commit_seq)."""
        if not self.items:
            return []
        q = np.asarray(self._embedder.embed([query])[0], dtype=np.float32)
        scores = self._vectors @ q
        order = sorted(range(len(self.items)), key=lambda i: (-float(scores[i]), str(self.items[i].stream_id),
                                                              self.items[i].commit_seq))
        return [NaiveItem(self.items[i].event_id, self.items[i].stream_id, self.items[i].commit_seq,
                          self.items[i].text, float(scores[i])) for i in order[:k]]


def build_naive_index(session: ScopedSession, key_provider: RootKeyProvider, stream_ids: Sequence[UUID],
                      embedder: Embedder) -> NaiveIndex:
    """Index every readable captured event of `stream_ids`, read through `session` (the task principal's session)."""
    items = []
    for stream in stream_ids:
        for e in read_stream(session, key_provider, stream):                 # refuses a stream not granted here
            if e.envelope.event_type not in CAPTURE_TYPES or not isinstance(e.body, dict):
                continue                                                      # not captured, or Shredded (erased)
            content = e.body.get("content")
            text = event_text(e.envelope.event_type, content) if isinstance(content, dict) else ""
            if text:
                items.append(NaiveItem(e.envelope.event_id, stream, e.envelope.commit_seq, text))
    vectors = (np.asarray(embedder.embed([i.text for i in items]), dtype=np.float32) if items
               else np.zeros((0, embedder.dim), dtype=np.float32))
    return NaiveIndex(items, vectors, embedder)


def render_naive_section(items: Sequence[NaiveItem]) -> str:
    """V's memory section: the items in rank order, numbered; "(none)" when empty."""
    return "\n".join(f"{n}. {it.text}" for n, it in enumerate(items, 1)) if items else "(none)"
