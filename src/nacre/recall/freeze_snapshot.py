"""
Functionality: Freeze a recall snapshot: the per-stream position vector and the generations every later recall step
  reads, inside one REPEATABLE READ, read-only scoped session whose grants were resolved in that same snapshot.
Owns: refusing a non-snapshot session and ungranted streams, reading each stream's highest committed commit_seq, its
  shred epoch, its active projection generation and its active index generation (with that generation's embedder),
  and the canonical (CBOR-ready, float-free) form of the snapshot recorded in frames and traces.
Public entry: freeze_snapshot(), Snapshot, SnapshotRefused
Decisions: D-0025, D-0024, D-0005
Assumptions: A-0037
Notes: D-0025 §1: the cross-stream snapshot is a SET of per-stream positions read in one consistent Postgres snapshot
  (A-0037), so it is consistent across streams without a global counter. Grants are confirmed by the snapshot session
  itself (open_scoped_session(snapshot=True) resolves access inside the RR transaction; owner, D-0024 decision 2): a
  stream that is not readable in THIS snapshot is refused, never silently dropped.
  A stream with no events has position 0. A stream never indexed has index generation None.
"""
from dataclasses import dataclass
from uuid import UUID

from nacre.scopes.open_scoped_session import ScopedSession


class SnapshotRefused(PermissionError):
    """Not a snapshot session, or a requested stream is not granted in this snapshot."""


@dataclass(frozen=True)
class StreamPosition:
    stream_id: UUID
    commit_seq: int
    shred_epoch: int
    projection_generation: int
    index_generation: int | None
    embedder_id: str | None


@dataclass(frozen=True)
class Snapshot:
    principal_id: UUID
    streams: tuple[StreamPosition, ...]          # sorted by stream id

    def position(self, stream_id: UUID) -> StreamPosition:
        return next(p for p in self.streams if p.stream_id == stream_id)

    def canonical(self) -> list:
        """Float-free, deterministic form for frames and traces (D-0008 CBOR subset)."""
        return [[str(p.stream_id), p.commit_seq, p.shred_epoch, p.projection_generation, p.index_generation,
                 p.embedder_id] for p in self.streams]


def freeze_snapshot(session: ScopedSession, stream_ids) -> Snapshot:
    """The snapshot for `stream_ids`, read once inside the caller's snapshot session."""
    if not session.snapshot:
        raise SnapshotRefused("recall needs a snapshot session (open_scoped_session(..., snapshot=True))")
    iso, ro = session.conn.execute("SELECT current_setting('transaction_isolation'), "
                                   "current_setting('transaction_read_only')").fetchone()
    if (iso, ro) != ("repeatable read", "on"):
        raise SnapshotRefused(f"snapshot session is {iso}, read_only={ro}")
    wanted = sorted(set(stream_ids), key=str)
    refused = [s for s in wanted if s not in session.access.read_streams]
    if refused:
        raise SnapshotRefused(f"no read grant in this snapshot for {sorted(map(str, refused))}")
    out = []
    for s in wanted:
        seq = session.conn.execute("SELECT coalesce(max(commit_seq), 0) FROM ledger.events WHERE stream_id = %s",
                                   (s,)).fetchone()[0]
        epoch = (session.conn.execute("SELECT epoch FROM keys.shred_epochs WHERE stream_id = %s", (s,)).fetchone()
                 or (0,))[0]
        pgen = session.conn.execute("SELECT interp.active_generation(%s)", (s,)).fetchone()[0]
        igen = session.conn.execute("SELECT recall.active_generation(%s)", (s,)).fetchone()[0]
        emb = session.conn.execute("SELECT embedder_id FROM recall.index_generations WHERE stream_id = %s "
                                   "AND generation = %s", (s, igen)).fetchone()
        out.append(StreamPosition(s, seq, epoch, pgen, igen if emb else None, emb[0] if emb else None))
    return Snapshot(principal_id=session.access.principal_id, streams=tuple(out))
