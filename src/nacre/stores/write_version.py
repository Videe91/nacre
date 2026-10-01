"""
Functionality: Write one interpretation version: its memory event (the truth) and its projection rows, atomically.
Owns: the version event body (op `version`, full structural record + content), the keyed MACs of the content and of
  every grounded span (MacPurpose.INTERP_MAC under the event's own data key), and the `interp` rows.
Public entry: write_version(), read_version_events(), edges_from_body(), Edge, VersionRecord, normalize,
  VERSION_ACTOR
Decisions: D-0017, D-0008, D-0004
Assumptions: A-0014
Notes: The ONLY writer of interp.versions / interp.edges. Everything the projection holds is derivable from the event
  body, so stores/rebuild_projection.py can recompute it. Rows are written in the caller's transaction right after
  the event, so the event and its rows commit together (D-0017). MACs use the version event's data key: shredding it
  makes the projection's MACs unverifiable and the content unreadable, while the structure stays (D-0017, D3).
  Spans are verified by the caller (the store that produced them); this file only records them.
"""
import uuid
from dataclasses import dataclass, field
from uuid import UUID

from nacre.core.encode_cbor import encode_cbor
from nacre.core.event import ActorKind, EventType, PayloadType, Source
from nacre.core.root_key_provider import RootKeyProvider
from nacre.keys.encrypt_payload import MacPurpose, derive_mac
from nacre.keys.get_or_create_key import load_key
from nacre.ledger.append_event import AppendRequest, Authorship, append_event
from nacre.ledger.read_stream import ReadEvent, read_stream
from nacre.scopes.open_scoped_session import ScopedSession

VERSION_ACTOR = uuid.UUID("6f2a1e4d-5b3c-4a2f-9e8d-7c6b5a4f3e2d")


def normalize(text: str) -> str:
    """The comparison form of a proposition (MNEXA: casefold + collapsed whitespace)."""
    return " ".join(text.casefold().split())


@dataclass(frozen=True)
class Edge:
    role: str
    target_event_id: UUID | None = None
    target_object_id: UUID | None = None
    target_version: int | None = None
    span: tuple[int, int] | None = None
    span_text: str | None = None


@dataclass(frozen=True)
class VersionRecord:
    object_id: UUID
    version: int
    kind: str
    status: str
    support: str | None
    content: dict
    edges: tuple[Edge, ...] = field(default=())


def _edge_body(e: Edge) -> dict:
    return {"role": e.role, "target_event_id": str(e.target_event_id) if e.target_event_id else None,
            "target_object_id": str(e.target_object_id) if e.target_object_id else None,
            "target_version": e.target_version, "span_start": e.span[0] if e.span else None,
            "span_end": e.span[1] if e.span else None, "span_text": e.span_text}


def edges_from_body(raw: list[dict]) -> list[Edge]:
    """The Edge objects of a version event body (inverse of what write_version records)."""
    return [Edge(x["role"], target_event_id=UUID(x["target_event_id"]) if x["target_event_id"] else None,
                 target_object_id=UUID(x["target_object_id"]) if x["target_object_id"] else None,
                 target_version=x["target_version"],
                 span=(x["span_start"], x["span_end"]) if x["span_start"] is not None else None,
                 span_text=x["span_text"]) for x in raw]


def write_version(session: ScopedSession, key_provider: RootKeyProvider, stream_id: UUID, rec: VersionRecord, *,
                  caused_by: UUID | None = None, cycle_id: UUID | None = None):
    """Append the version event and its projection rows; return the event's envelope."""
    body = {"op": "version", "object_id": str(rec.object_id), "version": rec.version, "kind": rec.kind,
            "status": rec.status, "support": rec.support, "content": rec.content,
            "edges": [_edge_body(e) for e in rec.edges]}
    env = append_event(session, key_provider, AppendRequest(
        stream_id=stream_id, event_type=EventType.MEMORY_EVENT, payload_type=PayloadType.STRUCTURED,
        actor_kind=ActorKind.SYSTEM, actor_id=VERSION_ACTOR, source=Source.SYSTEM,
        authorship=Authorship.SCOPE_PRINCIPAL, idempotency_key=str(uuid.uuid4()), content=body,
        caused_by=caused_by, cycle_id=cycle_id)).envelope
    key = load_key(session.conn, key_provider, env.key_id)
    session.conn.execute(
        "INSERT INTO interp.versions (object_id, version, kind, status, support, stream_id, event_id, commit_seq, "
        "content_mac) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (rec.object_id, rec.version, rec.kind, rec.status, rec.support, stream_id, env.event_id, env.commit_seq,
         derive_mac(key, MacPurpose.INTERP_MAC, b"content|" + encode_cbor(body))))
    for i, e in enumerate(rec.edges):
        span_mac = derive_mac(key, MacPurpose.INTERP_MAC, b"span|" + e.span_text.encode()) if e.span else None
        session.conn.execute(
            "INSERT INTO interp.edges (object_id, version, ordinal, stream_id, role, target_event_id, target_object_id, "
            "target_version, span_start, span_end, span_mac) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (rec.object_id, rec.version, i, stream_id, e.role, e.target_event_id, e.target_object_id, e.target_version,
             e.span[0] if e.span else None, e.span[1] if e.span else None, span_mac))
    return env


def read_version_events(session: ScopedSession, key_provider: RootKeyProvider, stream_id: UUID) -> list[ReadEvent]:
    """Every version event of the stream, in commit order (content decrypted; shredded ones have a Shredded body)."""
    return [e for e in read_stream(session, key_provider, stream_id) if e.envelope.event_type == EventType.MEMORY_EVENT
            and isinstance(e.body, dict) and isinstance(e.body.get("content"), dict)
            and e.body["content"].get("op") == "version"]
