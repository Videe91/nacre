"""
Functionality: Assemble the frozen ContextFrame: quorum pruning, budget fill in rank order, item contents (with the
  contradicting evidence of contested items), canonical CBOR and the frame_id.
Owns: the pruning rule, the budget fill, the frame's fields and canonical form, and frame_id = sha256(CBOR).
Public entry: assemble_frame(), frame_item(), content_view(), Frame, Budget, FRAME_VERSION
Decisions: D-0025, D-0008, D-0023
Assumptions: A-0036
Notes: D-0025 §5, §7 and amendment 1 (owner, 2026-10-02).
  - Pruning: a candidate is pruned only if AT LEAST TWO active channels place it outside their own top-M
    (M = 3 x budget items). With fewer than two active channels nothing is pruned for relevance.
  - Contested candidates are pinned against relevance pruning ("shown with its status", §5) but count against the
    budget and are filled after every uncontested one (they are ranked last, amendment 1). D1 reading of §5's "pinned
    whatever the budget" together with EXP-0004's fixed budget (10 items, 4,000 chars): the budget always holds.
  - Fill: in rank order until the item or character budget would be exceeded (characters = the item texts).
  - Items carry text, kind, status, scope level, the four scores, qualifiers and origin from the version content,
    and for contested items `contested: true` plus `contradicting`: the evidence event ids and the contradiction text
    recorded in the contested version (never phrased as fact: rendering is the interface's job, R21).
  - content_view(item): the pool-independent part of an item (no scores), which the trace MACs under the item's own
    key (D-0025 amendment 2), so other items still verify after one item is erased and the pool changes.
  - Canonical form: deterministic CBOR, no floats (D-0008); frame_id = sha256 of those bytes. The frame holds the
    query's sha256, never the query text (the trace stores the text under the requester's key, D-0025 §8).
"""
import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

from nacre.core.encode_cbor import encode_cbor
from nacre.core.root_key_provider import RootKeyProvider
from nacre.ledger.read_stream import read_stream
from nacre.recall.rank_candidates import Ranked
from nacre.scopes.open_scoped_session import ScopedSession

FRAME_VERSION = 1


@dataclass(frozen=True)
class Budget:
    items: int = 10
    chars: int = 4000


@dataclass(frozen=True)
class Frame:
    frame_id: str
    body: dict
    cbor: bytes


def _pruned(ranked: Sequence[Ranked], active: Mapping[str, bool], m: int) -> set[UUID]:
    on = [c for c in ("semantic", "lexical", "entity") if active.get(c)]
    if len(on) < 2:
        return set()
    tops = {c: {r.version_event_id for r in sorted(ranked, key=lambda r: (-getattr(r, c), str(r.version_event_id)))[:m]}
            for c in on}
    return {r.version_event_id for r in ranked
            if not r.contested and sum(1 for c in on if r.version_event_id not in tops[c]) >= 2}


def _version_content(session: ScopedSession, kp: RootKeyProvider, stream: UUID, seq: int) -> dict:
    (ev,) = read_stream(session, kp, stream, from_seq=seq, as_of=seq, limit=1)
    body = ev.body["content"] if isinstance(ev.body, dict) else {}
    return body.get("content", {}) if isinstance(body, dict) else {}


_CONTENT_FIELDS = ("version_event_id", "object_id", "version", "kind", "status", "scope_level", "text", "qualifiers",
                   "origin", "contested", "contradicting")


def frame_item(session: ScopedSession, key_provider: RootKeyProvider, c, text: str, r: Ranked | None) -> dict:
    """One frame item for candidate `c` (merge_scopes.Candidate); scores from `r` (None: replay of content only)."""
    content = _version_content(session, key_provider, c.stream_id, c.commit_seq)
    item = {"version_event_id": str(c.version_event_id), "object_id": str(c.object_id), "version": c.version,
            "kind": c.kind, "status": c.status, "scope_level": c.scope_level, "text": text,
            "qualifiers": [{"type": q.get("type"), "text": q.get("text")} for q in content.get("qualifiers", [])],
            "origin": content.get("origin"),
            "scores": ({"semantic": r.semantic, "lexical": r.lexical, "entity": r.entity, "fused_q": r.fused_q}
                       if r is not None else None),
            "contested": c.status == "contested"}
    if c.status == "contested":
        item["contradicting"] = {"event_ids": [str(e) for e in c.contradicting_event_ids],
                                 "text": content.get("contradiction_text")}
    return item


def content_view(item: dict) -> bytes:
    """Canonical bytes of the item's content (no scores): what the trace MACs under the item's own key."""
    return encode_cbor({k: item[k] for k in _CONTENT_FIELDS if k in item})


def assemble_frame(session: ScopedSession, key_provider: RootKeyProvider, *, snapshot, scopes, principal_id: UUID,
                   query_text: str, addresses: Sequence[str], relaxations: Sequence[str], candidates: Sequence,
                   ranked: Sequence[Ranked], active: Mapping[str, bool], texts: Mapping[UUID, str], coverage: str,
                   budget: Budget = Budget(), pipeline_version: str = "recall-v1", config: Mapping | None = None) -> Frame:
    """The frame for this recall (canonical bytes and id). `candidates` are merge_scopes.Candidate."""
    by_id = {c.version_event_id: c for c in candidates}
    pruned = _pruned(ranked, active, 3 * budget.items)
    items, used = [], 0
    for r in ranked:
        if r.version_event_id in pruned or len(items) >= budget.items:
            continue
        text = texts.get(r.version_event_id, "")
        if used + len(text) > budget.chars:
            continue
        item = frame_item(session, key_provider, by_id[r.version_event_id], text, r)
        items.append(item)
        used += len(text)
    body = {"v": FRAME_VERSION, "frame_kind": "context", "pipeline_version": pipeline_version,
            "snapshot": snapshot.canonical(), "scopes": [[lvl, str(s)] for lvl, s in scopes],
            "principal_id": str(principal_id), "query_sha256": hashlib.sha256(query_text.encode()).hexdigest(),
            "addresses": list(addresses), "relaxations": list(relaxations),
            "channels": {k: bool(v) for k, v in sorted(active.items())}, "coverage": coverage,
            "budget": {"items": budget.items, "chars": budget.chars}, "config": dict(config or {}), "items": items,
            "threats": [], "prediction": None}
    cbor = encode_cbor(body)
    return Frame(hashlib.sha256(cbor).hexdigest(), body, cbor)
