"""
Functionality: Rebuild a stream's `interp` projection from its ledger events into a new generation: recompute, compare
  with the active generation, and on a mismatch write the new generation and switch to it atomically.
Owns: deriving every versions/edges row (MACs included) from the version events alone, the comparison report, writing
  generation g+1, carrying over rows that cannot be recomputed (shredded), and the switch record.
Public entry: rebuild_projection(), ProjectionCheck
Decisions: D-0017
Assumptions: none
Notes: D-0017 amendment 2 (owner): recompute into a NEW generation; if identical to the active generation nothing is
  written; otherwise the new generation's rows AND the `interp.generation_switches` row are written in the caller's
  transaction, so readers see the old or the new generation, never a mix. The old generation is retained (append-only).
  Phase 2 gate item 8 = an identical rebuild. Shredded version events cannot be recomputed (no key): they are reported
  as `unverifiable`, are never counted as differences, and their stored rows are carried into the new generation
  unchanged, so a rebuild never silently drops structure.
"""
from dataclasses import dataclass, field
from uuid import UUID

from nacre.core.encode_cbor import encode_cbor
from nacre.core.event import EventType
from nacre.core.root_key_provider import RootKeyProvider
from nacre.keys.encrypt_payload import MacPurpose, derive_mac
from nacre.keys.get_or_create_key import load_key
from nacre.ledger.read_stream import read_stream
from nacre.scopes.open_scoped_session import ScopedSession

_V = "object_id, version, kind, status, support, stream_id, event_id, commit_seq, content_mac"
_E = "object_id, version, ordinal, stream_id, role, target_event_id, target_object_id, target_version, span_start, span_end, span_mac"


@dataclass
class ProjectionCheck:
    generation: int = 1
    expected_versions: int = 0
    expected_edges: int = 0
    differences: list[str] = field(default_factory=list)
    unverifiable: list[UUID] = field(default_factory=list)
    switched_to: int | None = None

    @property
    def identical(self) -> bool:
        return not self.differences


def _expected(session, key_provider, stream_id, check):
    exp_v, exp_e = {}, {}
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
        exp_v[(oid, ver)] = (oid, ver, body["kind"], body["status"], body["support"], stream_id, e.envelope.event_id,
                             e.envelope.commit_seq, derive_mac(key, MacPurpose.INTERP_MAC, b"content|" + encode_cbor(body)))
        for i, x in enumerate(body["edges"]):
            span = derive_mac(key, MacPurpose.INTERP_MAC, b"span|" + x["span_text"].encode()) if x["span_start"] is not None else None
            exp_e[(oid, ver, i)] = (oid, ver, i, stream_id, x["role"], UUID(x["target_event_id"]) if x["target_event_id"] else None,
                                    UUID(x["target_object_id"]) if x["target_object_id"] else None, x["target_version"],
                                    x["span_start"], x["span_end"], span)
    return exp_v, exp_e


def _stored(session, stream_id, gen):
    v = {(r[0], r[1]): tuple(r[:8]) + (bytes(r[8]),) for r in session.conn.execute(
        f"SELECT {_V} FROM interp.versions WHERE stream_id = %s AND generation = %s", (stream_id, gen))}
    e = {(r[0], r[1], r[2]): tuple(r[:10]) + (bytes(r[10]) if r[10] is not None else None,) for r in session.conn.execute(
        f"SELECT {_E} FROM interp.edges WHERE stream_id = %s AND generation = %s", (stream_id, gen))}
    return v, e


def rebuild_projection(session: ScopedSession, key_provider: RootKeyProvider, stream_id: UUID, *,
                       switch: bool = True) -> ProjectionCheck:
    """Compare the recomputed projection with the active generation; on a mismatch (and switch=True) write g+1 and switch."""
    gen = session.conn.execute("SELECT interp.active_generation(%s)", (stream_id,)).fetchone()[0]
    check = ProjectionCheck(generation=gen)
    exp_v, exp_e = _expected(session, key_provider, stream_id, check)
    act_v, act_e = _stored(session, stream_id, gen)
    hidden = set(check.unverifiable)
    carried_v = {k: v for k, v in act_v.items() if v[6] in hidden}
    carried_e = {k: v for k, v in act_e.items() if (k[0], k[1]) in carried_v}
    cmp_v = {k: v for k, v in act_v.items() if k not in carried_v}
    cmp_e = {k: v for k, v in act_e.items() if (k[0], k[1]) not in carried_v}
    check.expected_versions, check.expected_edges = len(exp_v), len(exp_e)
    for name, exp, act in (("version", exp_v, cmp_v), ("edge", exp_e, cmp_e)):
        for k in sorted(set(exp) | set(act), key=str):
            if exp.get(k) != act.get(k):
                state = "missing from store" if k not in act else "not derivable from the ledger" if k not in exp else "fields differ"
                check.differences.append(f"{name} {k}: {state}")
    if check.identical or not switch:
        return check
    new = gen + 1
    rows_v = sorted(list(exp_v.values()) + list(carried_v.values()), key=lambda r: (r[7], r[1]))
    for r in rows_v:
        session.conn.execute(f"INSERT INTO interp.versions ({_V}, generation) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", (*r, new))
    for r in sorted(list(exp_e.values()) + list(carried_e.values()), key=lambda r: (str(r[0]), r[1], r[2])):
        session.conn.execute(f"INSERT INTO interp.edges ({_E}, generation) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", (*r, new))
    session.conn.execute("INSERT INTO interp.generation_switches (stream_id, generation, reason, differences) "
                         "VALUES (%s, %s, 'rebuild mismatch', %s)", (stream_id, new, len(check.differences)))
    check.switched_to = new
    return check
