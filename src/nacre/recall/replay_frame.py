"""
Functionality: Replay a recall trace: reconstruct the frame at the recorded snapshot, verify each item's content MAC
  under the item's own key, report erased items as shredded, and compare the frame hash when nothing was erased.
Owns: rebuilding the Snapshot from the trace, the per-item verdicts, and the frame-hash comparison rule.
Public entry: replay_frame(), Replay
Decisions: D-0025, D-0023, D-0024
Assumptions: A-0036
Notes: D-0025 amendment 2 (owner, 2026-10-02):
  - Per item: `shredded` when the item's data key is gone (erasure); otherwise the item's content is rebuilt at the
    snapshot (merge_scopes bounded by the recorded positions, the index entry's text, the version's content) and its
    MAC recomputed under the item's own key: `verified` if equal, `mismatch` if not, `missing` if the version is no
    longer a candidate at that snapshot.
  - Frame hash: rebuilt with the recorded query, addresses, scopes, tau and budget and compared ONLY when no item is
    shredded and every stream's active index generation still equals the recorded one; otherwise `frame_match` is
    None (not comparable), never False.
  - An unreadable trace (the requester's key was erased) replays as `trace_unreadable`.
"""
from dataclasses import dataclass
from uuid import UUID

import psycopg

from nacre.core.embedder import Embedder
from nacre.core.root_key_provider import RootKeyProvider
from nacre.recall.assemble_frame import Budget, frame_item
from nacre.recall.freeze_snapshot import Snapshot, StreamPosition
from nacre.recall.load_index_cache import IndexCache
from nacre.recall.merge_scopes import merge_scopes
from nacre.recall.recall_context import RecallRequest, build_frame
from nacre.recall.record_context_assembled import item_mac, read_trace
from nacre.scopes.open_scoped_session import open_scoped_session


@dataclass(frozen=True)
class Replay:
    status: str                                   # "replayed" | "trace_unreadable"
    items: dict[UUID, str]                        # version_event_id -> verified | shredded | mismatch | missing
    frame_match: bool | None


def replay_frame(conn: psycopg.Connection, key_provider: RootKeyProvider, replayer_id: UUID, trace_stream: UUID,
                 trace_commit_seq: int, *, cache: IndexCache, embedder: Embedder) -> Replay:
    with open_scoped_session(conn, replayer_id, snapshot=True) as s:
        trace = read_trace(s, key_provider, trace_stream, trace_commit_seq)
        if trace is None:
            return Replay("trace_unreadable", {}, None)
        snapshot = Snapshot(trace.principal_id, tuple(
            StreamPosition(UUID(sid), seq, epoch, pgen, igen, emb) for sid, seq, epoch, pgen, igen, emb in trace.snapshot))
        cands = {c.version_event_id: c for c in merge_scopes(s, snapshot, trace.scopes)}
        wanted: dict[UUID, list[UUID]] = {}
        for it in trace.items:
            wanted.setdefault(it.stream_id, []).append(it.version_event_id)
        served = cache.entries(s, wanted)
        verdicts: dict[UUID, str] = {}
        for it in trace.items:
            key_alive = s.conn.execute("SELECT 1 FROM ledger.events e JOIN keys.data_keys k ON k.key_id = e.key_id "
                                       "WHERE e.event_id = %s", (it.version_event_id,)).fetchone()
            entry = served.get(it.stream_id, {}).get(it.version_event_id)
            if not key_alive:
                verdicts[it.version_event_id] = "shredded"
            elif it.version_event_id not in cands or entry is None:
                verdicts[it.version_event_id] = "missing"
            else:
                item = frame_item(s, key_provider, cands[it.version_event_id], entry.text, None)
                mac = item_mac(s, key_provider, it.version_event_id, item)
                verdicts[it.version_event_id] = "verified" if mac == it.mac else "mismatch"
        same_generations = all(
            p.index_generation == s.conn.execute("SELECT recall.active_generation(%s)", (p.stream_id,)).fetchone()[0]
            for p in snapshot.streams if p.index_generation is not None)
        frame_match = None
        if "shredded" not in verdicts.values() and same_generations:
            request = RecallRequest(trace_stream, trace.scopes, trace.query, trace.addresses)
            rebuilt = build_frame(s, key_provider, snapshot, request, principal_id=trace.principal_id, cache=cache,
                                  embedder=embedder, tau_strong_q=trace.config["tau_strong_q"],
                                  budget=Budget.of(trace.budget),
                                  config_version=trace.config["config_version"])
            frame_match = rebuilt.frame_id == trace.frame_id
    return Replay("replayed", verdicts, frame_match)
