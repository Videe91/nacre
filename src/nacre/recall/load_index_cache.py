"""
Functionality: Serve decrypted recall-index entries from a per-process cache, only inside a confirmed recall snapshot.
Owns: the cache (per stream: generation, embedder, epoch, entries tagged by stream and key), the snapshot and grant
  preconditions, eviction of destroyed keys on a shred-epoch change, reload on a generation or embedder change,
  fetching and decrypting missing entries, and the LRU memory cap.
Public entry: IndexCache, CacheRefused
Decisions: D-0024, D-0025, D-0005
Assumptions: A-0032, A-0033, A-0035
Notes: The cache lives outside Postgres RLS, so it enforces scope itself (owner, D-0024 decision 2):
  - it serves only inside a snapshot session (open_scoped_session(snapshot=True): REPEATABLE READ, read only), whose
    access was resolved from the grants IN THAT SNAPSHOT. A stream not in that snapshot's read set is refused
    (CacheRefused), even if another principal's recall loaded it. A revoked grant therefore serves nothing from the
    next snapshot on;
  - every call reads keys.shred_epochs inside the snapshot. If a stream's epoch differs from the one the cache last
    validated, it asks which cached key_ids still exist and evicts the rest BEFORE serving. So no recall whose
    snapshot begins after an erasure commits is served erased content, in any process (each process has its own
    cache and makes the same check).
  - Entries are fetched by version id (the heads the caller's snapshot names), not by insertion order: an identity
    sequence is assigned before commit, so "everything after seq N" could miss a late commit. (D1)
  - An older snapshot may re-add an entry a newer one evicted (its key still exists in that snapshot); it then
    records its own, older epoch, so the next newer snapshot re-validates and evicts again. (D1)
  - Plaintext stays in process memory only (A-0033: no swap, no core dumps in deployment).
"""
import threading
from collections import OrderedDict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from uuid import UUID

from nacre.core.root_key_provider import RootKeyProvider
from nacre.keys.get_or_create_key import load_key
from nacre.recall.index_version import IndexEntry, open_entry
from nacre.scopes.open_scoped_session import ScopedSession

DEFAULT_MAX_BYTES = 512 * 2**20


class CacheRefused(PermissionError):
    """The cache was asked to serve outside a snapshot session, or for a stream the snapshot does not grant."""


@dataclass
class _Stream:
    generation: int
    embedder_id: str
    epoch: int
    entries: dict[UUID, IndexEntry] = field(default_factory=dict)
    nbytes: int = 0


def _size(e: IndexEntry) -> int:
    return e.embedding.nbytes + len(e.text.encode()) + sum(len(a) for a in e.addresses) + 64


class IndexCache:
    def __init__(self, key_provider: RootKeyProvider, *, dim: int, max_bytes: int = DEFAULT_MAX_BYTES):
        self._kp, self._dim, self._max = key_provider, dim, max_bytes
        self._streams: OrderedDict[UUID, _Stream] = OrderedDict()
        self._lock = threading.Lock()

    def entries(self, session: ScopedSession, wanted: Mapping[UUID, Iterable[UUID]]) -> dict[UUID, dict[UUID, IndexEntry]]:
        """For each stream, the requested versions' entries that exist and are readable in this snapshot."""
        _require_snapshot(session)
        refused = [s for s in wanted if s not in session.access.read_streams]
        if refused:
            raise CacheRefused(f"no read grant in this snapshot for streams {sorted(map(str, refused))}")
        with self._lock:
            out = {s: self._serve(session, s, set(v)) for s, v in wanted.items()}
            self._evict_lru()
        return out

    def _serve(self, session: ScopedSession, stream: UUID, vids: set[UUID]) -> dict[UUID, IndexEntry]:
        conn = session.conn
        gen = conn.execute("SELECT recall.active_generation(%s)", (stream,)).fetchone()[0]
        row = conn.execute("SELECT embedder_id FROM recall.index_generations WHERE stream_id = %s AND generation = %s",
                           (stream, gen)).fetchone()
        if row is None:
            self._streams.pop(stream, None)
            return {}
        epoch = (conn.execute("SELECT epoch FROM keys.shred_epochs WHERE stream_id = %s", (stream,)).fetchone()
                 or (0,))[0]
        sc = self._streams.get(stream)
        if sc is None or (sc.generation, sc.embedder_id) != (gen, row[0]):
            sc = self._streams[stream] = _Stream(gen, row[0], epoch)
        elif sc.epoch != epoch:
            self._evict_destroyed(conn, sc)
            sc.epoch = epoch
        self._streams.move_to_end(stream)
        missing = [v for v in vids if v not in sc.entries]
        if missing:
            self._load(session, stream, sc, missing)
            sc.epoch = epoch
        return {v: sc.entries[v] for v in vids if v in sc.entries}

    def _evict_destroyed(self, conn, sc: _Stream) -> None:
        keys = {e.key_id for e in sc.entries.values()}
        alive = {k for (k,) in conn.execute("SELECT key_id FROM keys.data_keys WHERE key_id = ANY(%s)", (list(keys),))}
        for vid in [v for v, e in sc.entries.items() if e.key_id not in alive]:
            sc.nbytes -= _size(sc.entries.pop(vid))

    def _load(self, session: ScopedSession, stream: UUID, sc: _Stream, vids: list[UUID]) -> None:
        rows = session.conn.execute(
            "SELECT version_event_id, key_id, embedder_id, body FROM recall.index_entries "
            "WHERE stream_id = %s AND index_generation = %s AND version_event_id = ANY(%s)",
            (stream, sc.generation, vids)).fetchall()
        keys = {}
        for vid, key_id, embedder_id, body in rows:
            if key_id not in keys:
                keys[key_id] = load_key(session.conn, self._kp, key_id)
            if keys[key_id] is None:            # destroyed: the entry is noise now (D-0024 §2)
                continue
            e = open_entry(keys[key_id], stream, sc.generation, vid, embedder_id, bytes(body), self._dim)
            sc.entries[vid] = e
            sc.nbytes += _size(e)

    def _evict_lru(self) -> None:
        while self._streams and sum(s.nbytes for s in self._streams.values()) > self._max:
            self._streams.popitem(last=False)

    def cached_streams(self) -> list[UUID]:
        """Streams currently held, least recently used first (for tests and metrics; no content)."""
        with self._lock:
            return list(self._streams)


def _require_snapshot(session: ScopedSession) -> None:
    if not session.snapshot:
        raise CacheRefused("the recall cache serves only inside a snapshot session (D-0025 §1)")
    iso, ro = session.conn.execute("SELECT current_setting('transaction_isolation'), "
                                   "current_setting('transaction_read_only')").fetchone()
    if (iso, ro) != ("repeatable read", "on"):
        raise CacheRefused(f"snapshot session is {iso}, read_only={ro}; expected repeatable read, read only")
