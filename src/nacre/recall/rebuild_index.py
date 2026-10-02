"""
Functionality: Rebuild a stream's recall index from its ledger version events into a new generation: recompute,
  compare with the active generation, and on a difference (or an embedder change) write generation g+1 and switch.
Owns: holding the stream's append lock for the whole rebuild, recomputing every readable version's entry plaintext,
  the comparison (exact plaintext equality), the report, writing g+1 and the switch row in one transaction, and
  back-filling generation 1 for a stream that has none yet.
Public entry: rebuild_index(), IndexCheck
Decisions: D-0024, D-0017
Assumptions: A-0034
Notes: D-0024 §2 (owner decision 4): a model change triggers a FULL re-index into a new generation; the old one is
  kept (append-only) and never mixed with the new one in a search. Same pattern as rebuild_projection (D-0017
  amendment 2): identical -> nothing written; otherwise g+1 + `recall.index_switches` in the caller's transaction, so
  a recall sees the old or the new generation, never a mix.
  - The stream's append lock (the one append_event takes) is held from the start, so no version can be written
    between the recompute and the switch.
  - The version list comes from the `interp` projection's active generation (structure survives shredding), so a
    shredded version is counted. Versions whose key is destroyed cannot be recomputed: reported `unverifiable`, never a difference, and given NO
    entry in g+1 (their old ciphertext is bound to its generation by the AAD, and lost versions are never
    recall-eligible, D-0023 §5). (D1)
  - Comparison is exact on the decrypted plaintext (same embedder and runtime are deterministic); an entry missing
    from the active generation is a difference. (D1)
"""
from dataclasses import dataclass, field
from uuid import UUID

from nacre.core.embedder import Embedder
from nacre.core.root_key_provider import RootKeyProvider
from nacre.keys.get_or_create_key import load_key
from nacre.recall.index_version import default_embedder, entry_plaintext, index_version, open_plaintext
from nacre.scopes.open_scoped_session import ScopedSession
from nacre.stores.write_version import read_version_events


@dataclass
class IndexCheck:
    generation: int = 1
    embedder_id: str | None = None
    versions: int = 0
    differences: list[UUID] = field(default_factory=list)
    unverifiable: list[UUID] = field(default_factory=list)
    switched_to: int | None = None
    reason: str | None = None
    backfilled: bool = False

    @property
    def identical(self) -> bool:
        return not self.differences and self.reason != "embedder_change"


def rebuild_index(session: ScopedSession, key_provider: RootKeyProvider, stream_id: UUID,
                  embedder: Embedder | None = None) -> IndexCheck:
    """Recompute the stream's index; switch to a new generation if it differs or the embedder changed."""
    embedder = embedder or default_embedder()
    conn = session.conn
    conn.execute("SELECT pg_advisory_xact_lock(ledger.stream_lock_key(%s))", (stream_id,))
    gen = conn.execute("SELECT recall.active_generation(%s)", (stream_id,)).fetchone()[0]
    row = conn.execute("SELECT embedder_id FROM recall.index_generations WHERE stream_id = %s AND generation = %s",
                       (stream_id, gen)).fetchone()
    check = IndexCheck(generation=gen, embedder_id=row[0] if row else None)
    versions = [r[0] for r in conn.execute(
        "SELECT event_id FROM interp.versions WHERE stream_id = %s AND generation = interp.active_generation(%s) "
        "ORDER BY commit_seq", (stream_id, stream_id))]
    readable, stored = [], {}
    by_id = {ev.envelope.event_id: ev for ev in read_version_events(session, key_provider, stream_id)}
    for vid in versions:
        check.versions += 1
        ev = by_id.get(vid)
        key = load_key(conn, key_provider, ev.envelope.key_id) if ev else None
        if key is None:                               # shredded: the projection keeps the structure, not the content
            check.unverifiable.append(vid)
            continue
        c = ev.body["content"]
        readable.append((ev.envelope, key, c["kind"], c["content"]))
    if row is None:                                   # nothing indexed yet: back-fill generation 1, no switch
        for env, _, kind, content in readable:
            index_version(session, key_provider, stream_id, env.event_id, env.key_id, kind, content, embedder)
        check.embedder_id, check.backfilled = embedder.embedder_id, True
        return check
    for vid, body in conn.execute("SELECT version_event_id, body FROM recall.index_entries "
                                  "WHERE stream_id = %s AND index_generation = %s", (stream_id, gen)):
        stored[vid] = bytes(body)
    if embedder.embedder_id != check.embedder_id:
        check.reason = "embedder_change"
    else:
        for env, key, kind, content in readable:
            body = stored.get(env.event_id)
            if body is None or open_plaintext(key, stream_id, gen, env.event_id, check.embedder_id, body) != \
                    entry_plaintext(kind, content, embedder):
                check.differences.append(env.event_id)
        if not check.differences:
            return check
        check.reason = "rebuild"
    new = gen + 1
    for env, _, kind, content in readable:
        index_version(session, key_provider, stream_id, env.event_id, env.key_id, kind, content, embedder,
                      generation=new)
    conn.execute("INSERT INTO recall.index_switches (stream_id, generation, reason) VALUES (%s, %s, %s)",
                 (stream_id, new, check.reason))
    check.switched_to = new
    return check
