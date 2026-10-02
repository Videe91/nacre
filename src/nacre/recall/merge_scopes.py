"""
Functionality: Merge the requested scopes into the recall candidate pool: the recall-eligible interpretation heads of
  each granted stream at the frozen snapshot, tagged with their scope level and, when contested, their contradicting
  evidence.
Owns: the Phase 3 recall-eligibility rule (D-0025 §2 + amendment 1), reading heads from each stream's ACTIVE projection
  generation at or before the snapshot position, excluding lost heads (key destroyed), attaching contradiction edges
  to contested heads, and the narrowest-first scope ranking.
Public entry: merge_scopes(), Candidate, ELIGIBLE_STATUSES
Decisions: D-0025, D-0017, D-0023, D-0005
Assumptions: A-0037
Notes: D-0025 amendment 1 (owner, 2026-10-02): Phase 3 recall INCLUDES contested heads, labelled `contested`, with the
  contradicting evidence attached here (the `contradiction` edges of the contested version: the agreeing contradiction
  proposals); ranking below uncontested and the "never phrased as fact" rendering are later stages (rank, assemble,
  render). stores/read_heads.py keeps D-0017's rule for Phase 2 reads and is not used here.
  - Eligible: status active / contested / fallback (beliefs and fallbacks) and active episodes. Superseded heads,
    unpromoted proposals (not versions) and lost heads (their data key is destroyed, D-0023 §5) are never eligible.
  - A head is the highest version of an object in the stream's active projection generation with commit_seq at or
    below the snapshot position (the RR snapshot already hides later commits; the bound is kept explicit).
  - Scope level: the request lists (level, stream) narrowest first; level_rank 0 is the narrowest. No cross-scope
    shadowing in Phase 3 (D-0025 decision 2): every eligible head appears, labelled with its scope.
  - Streams must be in the snapshot (granted and frozen by freeze_snapshot); anything else is refused.
"""
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from nacre.recall.freeze_snapshot import Snapshot
from nacre.scopes.open_scoped_session import ScopedSession

ELIGIBLE_STATUSES = ("active", "contested", "fallback")


class MergeRefused(PermissionError):
    """A requested stream is not part of the frozen snapshot."""


@dataclass(frozen=True)
class Candidate:
    version_event_id: UUID
    object_id: UUID
    version: int
    kind: str
    status: str
    stream_id: UUID
    scope_level: str
    level_rank: int
    commit_seq: int
    key_id: UUID
    contradicting_event_ids: tuple[UUID, ...] = ()

    @property
    def contested(self) -> bool:
        return self.status == "contested"


def merge_scopes(session: ScopedSession, snapshot: Snapshot, scopes: Sequence[tuple[str, UUID]]) -> list[Candidate]:
    """The candidate pool across `scopes` ((level, stream) pairs, narrowest first) at `snapshot`."""
    frozen = {p.stream_id: p for p in snapshot.streams}
    missing = [s for _, s in scopes if s not in frozen]
    if missing:
        raise MergeRefused(f"streams not in the frozen snapshot: {sorted(map(str, missing))}")
    conn, out = session.conn, []
    for rank, (level, stream) in enumerate(scopes):
        pos = frozen[stream]
        rows = conn.execute(
            "SELECT DISTINCT ON (v.object_id) v.object_id, v.version, v.kind, v.status, v.commit_seq, v.event_id, "
            "e.key_id FROM interp.versions v JOIN ledger.events e ON e.event_id = v.event_id "
            "WHERE v.stream_id = %s AND v.generation = %s AND v.commit_seq <= %s "
            "ORDER BY v.object_id, v.version DESC", (stream, pos.projection_generation, pos.commit_seq)).fetchall()
        rows = [r for r in rows if r[3] in ELIGIBLE_STATUSES and (r[2] != "episode" or r[3] == "active")]
        alive = {k for (k,) in conn.execute("SELECT key_id FROM keys.data_keys WHERE key_id = ANY(%s)",
                                            ([r[6] for r in rows],))}
        contested = [r for r in rows if r[3] == "contested"]
        evidence: dict[tuple[UUID, int], list[UUID]] = {}
        if contested:
            for obj, ver, target in conn.execute(
                    "SELECT object_id, version, target_event_id FROM interp.edges WHERE stream_id = %s "
                    "AND generation = %s AND role = 'contradiction' AND object_id = ANY(%s) ORDER BY ordinal",
                    (stream, pos.projection_generation, [r[0] for r in contested])):
                evidence.setdefault((obj, ver), []).append(target)
        for obj, ver, kind, status, seq, event_id, key_id in rows:
            if key_id not in alive:
                continue                                    # lost (D-0023 §5): never recall-eligible
            out.append(Candidate(event_id, obj, ver, kind, status, stream, level, rank, seq, key_id,
                                 tuple(evidence.get((obj, ver), ())) if status == "contested" else ()))
    return out
