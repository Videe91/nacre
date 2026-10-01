"""
Functionality: Recompute a stream's `interp` projection from its ledger events and compare it with the stored rows.
Owns: deriving every versions/edges row (MACs included) from the version events alone, and reporting any difference.
Public entry: rebuild_projection(), ProjectionCheck
Decisions: D-0017
Assumptions: none
Notes: Phase 2 gate item 8. D-0017's text says the rebuild "drops the scope's projection rows and replays", but the
  same ADR makes the tables append-only (no DELETE for anyone), so a rebuild here RECOMPUTES the projection and
  compares it row by row (identical = pass). Repopulating an empty database from the ledger uses the same recompute.
  Flagged to the owner (CURRENT.md). Shredded version events cannot be recomputed (no key): they are reported as
  `unverifiable`, not as mismatches.
"""
from dataclasses import dataclass, field
from uuid import UUID

from nacre.core.encode_cbor import encode_cbor
from nacre.core.root_key_provider import RootKeyProvider
from nacre.keys.encrypt_payload import MacPurpose, derive_mac
from nacre.keys.get_or_create_key import load_key
from nacre.core.event import EventType
from nacre.ledger.read_stream import read_stream
from nacre.scopes.open_scoped_session import ScopedSession


@dataclass
class ProjectionCheck:
    expected_versions: int = 0
    expected_edges: int = 0
    differences: list[str] = field(default_factory=list)
    unverifiable: list[UUID] = field(default_factory=list)

    @property
    def identical(self) -> bool:
        return not self.differences


def rebuild_projection(session: ScopedSession, key_provider: RootKeyProvider, stream_id: UUID) -> ProjectionCheck:
    """Recompute the projection of `stream_id` from the ledger and compare with interp.versions / interp.edges."""
    check, exp_v, exp_e = ProjectionCheck(), {}, {}
    for e in read_stream(session, key_provider, stream_id):
        if e.envelope.event_type != EventType.MEMORY_EVENT:
            continue
        if not isinstance(e.body, dict):
            check.unverifiable.append(e.envelope.event_id)
            continue
        body = e.body["content"]
        if not (isinstance(body, dict) and body.get("op") == "version"):
            continue
        key = load_key(session.conn, key_provider, e.envelope.key_id)
        oid, ver = UUID(body["object_id"]), body["version"]
        exp_v[(oid, ver)] = (body["kind"], body["status"], body["support"], stream_id, e.envelope.event_id,
                             e.envelope.commit_seq, derive_mac(key, MacPurpose.INTERP_MAC, b"content|" + encode_cbor(body)))
        for i, x in enumerate(body["edges"]):
            span_mac = derive_mac(key, MacPurpose.INTERP_MAC, b"span|" + x["span_text"].encode()) if x["span_start"] is not None else None
            exp_e[(oid, ver, i)] = (stream_id, x["role"], UUID(x["target_event_id"]) if x["target_event_id"] else None,
                                    UUID(x["target_object_id"]) if x["target_object_id"] else None, x["target_version"],
                                    x["span_start"], x["span_end"], span_mac)
    act_v = {(r[0], r[1]): (r[2], r[3], r[4], r[5], r[6], r[7], bytes(r[8])) for r in session.conn.execute(
        "SELECT object_id, version, kind, status, support, stream_id, event_id, commit_seq, content_mac "
        "FROM interp.versions WHERE stream_id = %s", (stream_id,))}
    act_e = {(r[0], r[1], r[2]): (r[3], r[4], r[5], r[6], r[7], r[8], r[9], bytes(r[10]) if r[10] is not None else None)
             for r in session.conn.execute(
                 "SELECT object_id, version, ordinal, stream_id, role, target_event_id, target_object_id, target_version, "
                 "span_start, span_end, span_mac FROM interp.edges WHERE stream_id = %s", (stream_id,))}
    hidden = set(check.unverifiable)                              # shredded: reported, not compared
    act_v = {k: v for k, v in act_v.items() if v[4] not in hidden}
    keep = set(act_v) | set(exp_v)
    act_e = {k: v for k, v in act_e.items() if (k[0], k[1]) in keep}
    check.expected_versions, check.expected_edges = len(exp_v), len(exp_e)
    for name, exp, act in (("version", exp_v, act_v), ("edge", exp_e, act_e)):
        for k in sorted(set(exp) | set(act), key=str):
            if exp.get(k) != act.get(k):
                check.differences.append(f"{name} {k}: expected {'present' if k in exp else 'absent'}, "
                                         f"stored {'present' if k in act else 'absent'}{'' if k not in exp or k not in act else ', fields differ'}")
    return check
