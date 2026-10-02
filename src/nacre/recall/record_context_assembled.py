"""
Functionality: Record the ContextAssembled trace of one recall: a single event in the stream the recall was issued
  from, holding the query (under the requester's key), the snapshot, config and embedder versions, per-item references
  with content MACs keyed under each item's own derived key, and the frame hash. No recalled content is copied.
Owns: the trace body, the per-item MACs (MacPurpose.TRACE_MAC over content_view under the item version's data key),
  the requester-key rule, and reading a trace back.
Public entry: record_context_assembled(), read_trace(), Trace, TraceItem, TRACE_OP
Decisions: D-0025, D-0023, D-0012, D-0026
Assumptions: A-0036
Notes: D-0025 amendment 2 (owner, 2026-10-02, D3) replaces §8's content parts:
  - ONE memory_event (op `context_assembled`) in the ISSUING stream, written by the requesting principal in its own
    read-write scoped session; no privileged cross-stream writer.
  - The event is under the requester's key (D-0023 §6: the person's key when the agent acts `on_behalf_of` a person,
    the person's own key when a person asks, else the stream's system subject). It is not derived from recalled
    content, so `sources` is explicitly empty (the D-0023 structure test requires it to be named).
  - Per item: event id, version, stream, and HMAC(TRACE_MAC sub-key of the ITEM'S OWN data key, content_view(item)).
    Erasing a contributor destroys that key, so the MAC can no longer be verified: the item replays as shredded.
    content_view has no scores, so other items still verify after the pool changes (R20).
  - The trace is committed in the caller's transaction BEFORE the frame is returned (recall_context, R19).
"""
import uuid
from dataclasses import dataclass
from uuid import UUID

from nacre.core.event import ActorKind, EventType, PayloadType, Source
from nacre.core.root_key_provider import RootKeyProvider
from nacre.keys.encrypt_payload import MacPurpose, derive_mac
from nacre.keys.get_or_create_key import load_key
from nacre.ledger.append_event import AppendRequest, Authorship, append_event
from nacre.ledger.read_stream import read_stream
from nacre.recall.assemble_frame import Frame, content_view
from nacre.scopes.open_scoped_session import ScopedSession

TRACE_OP = "context_assembled"


@dataclass(frozen=True)
class TraceItem:
    version_event_id: UUID
    version: int
    stream_id: UUID
    mac: bytes


@dataclass(frozen=True)
class Trace:
    event_id: UUID
    frame_id: str
    query: str
    addresses: tuple[str, ...]
    scopes: tuple[tuple[str, UUID], ...]
    snapshot: list
    config_version: str
    items: tuple[TraceItem, ...]
    principal_id: UUID
    config: dict
    budget: dict


def item_mac(session: ScopedSession, key_provider: RootKeyProvider, version_event_id: UUID, item: dict) -> bytes | None:
    """The item's content MAC under its own version key; None when that key is destroyed (shredded)."""
    key_id = session.conn.execute("SELECT key_id FROM ledger.events WHERE event_id = %s", (version_event_id,)).fetchone()
    key = load_key(session.conn, key_provider, key_id[0]) if key_id else None
    return derive_mac(key, MacPurpose.TRACE_MAC, b"item|" + content_view(item)) if key is not None else None


def record_context_assembled(session: ScopedSession, key_provider: RootKeyProvider, *, issuing_stream: UUID,
                             frame: Frame, query_text: str, addresses, scopes, config_version: str,
                             actor_id: UUID, actor_kind: ActorKind = ActorKind.AGENT, source: Source = Source.CHAT,
                             authorship: Authorship = Authorship.EXTERNAL, on_behalf_of: UUID | None = None):
    """Append the trace event; returns its envelope. Raises if an item's key is already gone (never trace a frame
    whose content cannot be bound)."""
    stream_of = {UUID(s): seq for s, seq, *_ in frame.body["snapshot"]}
    items = []
    for it in frame.body["items"]:
        vid = UUID(it["version_event_id"])
        row = session.conn.execute("SELECT stream_id FROM ledger.events WHERE event_id = %s", (vid,)).fetchone()
        mac = item_mac(session, key_provider, vid, it)
        if row is None or mac is None or row[0] not in stream_of:
            raise ValueError(f"frame item {vid} cannot be bound to its key at trace time")
        items.append({"event_id": str(vid), "version": it["version"], "stream_id": str(row[0]), "mac": mac.hex()})
    content = {"op": TRACE_OP, "frame_id": frame.frame_id, "principal_id": frame.body["principal_id"],
               "config": frame.body["config"], "budget": frame.body["budget"],
               "query": query_text, "addresses": list(addresses),
               "scopes": [[lvl, str(s)] for lvl, s in scopes], "snapshot": frame.body["snapshot"],
               "config_version": config_version, "pipeline_version": frame.body["pipeline_version"], "items": items}
    return append_event(session, key_provider, AppendRequest(
        stream_id=issuing_stream, event_type=EventType.MEMORY_EVENT, payload_type=PayloadType.STRUCTURED,
        actor_kind=actor_kind, actor_id=actor_id, source=source, authorship=authorship, on_behalf_of=on_behalf_of,
        idempotency_key=str(uuid.uuid4()), content=content, sources=())).envelope


def read_trace(session: ScopedSession, key_provider: RootKeyProvider, stream_id: UUID, commit_seq: int) -> Trace | None:
    """The trace at (stream, commit_seq); None when its body is unreadable (the requester's key was erased)."""
    (ev,) = read_stream(session, key_provider, stream_id, from_seq=commit_seq, as_of=commit_seq, limit=1)
    body = ev.body.get("content") if isinstance(ev.body, dict) else None
    if not isinstance(body, dict) or body.get("op") != TRACE_OP:
        return None
    return Trace(ev.envelope.event_id, body["frame_id"], body["query"], tuple(body["addresses"]),
                 tuple((lvl, UUID(s)) for lvl, s in body["scopes"]), body["snapshot"], body["config_version"],
                 tuple(TraceItem(UUID(i["event_id"]), i["version"], UUID(i["stream_id"]), bytes.fromhex(i["mac"]))
                       for i in body["items"]),
                 UUID(body["principal_id"]), body["config"], body["budget"])
