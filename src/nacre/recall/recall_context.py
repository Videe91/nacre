"""
Functionality: Recall context for one request, end to end: freeze a grant-confirmed snapshot, build the frame through
  every recall stage, then commit the ContextAssembled trace before returning the frame.
Owns: the request type, the stage order and the hand-offs between stages, the two transactions (a read-only snapshot,
  then the trace write), and build_frame() (also used by replay).
Public entry: recall_context(), build_frame(), RecallRequest, RecallResult, PIPELINE_VERSION
Decisions: D-0025, D-0024, D-0023
Assumptions: A-0004, A-0032, A-0036, A-0037
Notes: D-0025. Stages: freeze_snapshot (R12) -> merge_scopes (R13) -> IndexCache.entries (R10) -> narrow_by_identity
  (R14) -> embed the query -> rank_candidates (R15) -> assess_coverage (R17) -> assemble_frame (R16), all inside ONE
  snapshot session (REPEATABLE READ, read only, grants resolved there); then record_context_assembled (R18) in a normal
  read-write session on the issuing stream. The frame is returned only after the trace commits: no untraced frame
  leaves this function (§8).
  - A candidate with no index entry (a version written before the index existed and not yet back-filled) is skipped.
  - tau_strong_q and the budget are recorded in the frame's `config` and in the trace, so replay uses the same values.
"""
from dataclasses import dataclass, field
from uuid import UUID

import psycopg

from nacre.core.embedder import Embedder
from nacre.core.event import ActorKind, Source
from nacre.core.root_key_provider import RootKeyProvider
from nacre.ledger.append_event import Authorship
from nacre.recall.assemble_frame import Budget, Frame, assemble_frame
from nacre.recall.assess_coverage import assess_coverage
from nacre.recall.freeze_snapshot import Snapshot, freeze_snapshot
from nacre.recall.load_index_cache import IndexCache
from nacre.recall.merge_scopes import merge_scopes
from nacre.recall.narrow_by_identity import narrow_by_identity
from nacre.recall.rank_candidates import rank_candidates
from nacre.recall.record_context_assembled import record_context_assembled
from nacre.scopes.open_scoped_session import ScopedSession, open_scoped_session

PIPELINE_VERSION = "recall-v1"


@dataclass(frozen=True)
class RecallRequest:
    issuing_stream: UUID
    scopes: tuple[tuple[str, UUID], ...]          # (level, stream), narrowest first
    query: str
    addresses: tuple[str, ...] = ()
    on_behalf_of: UUID | None = None


@dataclass(frozen=True)
class RecallResult:
    frame: Frame
    trace_event_id: UUID
    trace_stream: UUID
    trace_commit_seq: int
    coverage: str = field(default="")


def build_frame(session: ScopedSession, key_provider: RootKeyProvider, snapshot: Snapshot, request: RecallRequest, *,
                principal_id: UUID, cache: IndexCache, embedder: Embedder, tau_strong_q: int, budget: Budget,
                config_version: str) -> Frame:
    """Every recall stage at `snapshot`, inside the caller's snapshot session."""
    cands = merge_scopes(session, snapshot, request.scopes)
    wanted: dict[UUID, list[UUID]] = {}
    for c in cands:
        wanted.setdefault(c.stream_id, []).append(c.version_event_id)
    served = cache.entries(session, wanted)
    entries = {v: e for per in served.values() for v, e in per.items()}
    cands = [c for c in cands if c.version_event_id in entries]
    narrowed = narrow_by_identity([c.version_event_id for c in cands],
                                  {v: e.addresses for v, e in entries.items()}, request.addresses)
    keep = set(narrowed.version_event_ids)
    cands = [c for c in cands if c.version_event_id in keep]
    texts = {c.version_event_id: entries[c.version_event_id].text for c in cands}
    ranked, active = rank_candidates(cands, texts, {c.version_event_id: entries[c.version_event_id].embedding
                                                    for c in cands},
                                     request.query, embedder.embed([request.query])[0], narrowed.exact_matches,
                                     bool(request.addresses))
    coverage = assess_coverage(ranked, active, tau_strong_q=tau_strong_q)
    return assemble_frame(session, key_provider, snapshot=snapshot, scopes=request.scopes, principal_id=principal_id,
                          query_text=request.query, addresses=request.addresses, relaxations=narrowed.relaxations,
                          candidates=cands, ranked=ranked, active=active, texts=texts, coverage=coverage.value,
                          budget=budget, pipeline_version=PIPELINE_VERSION,
                          config={"config_version": config_version, "tau_strong_q": tau_strong_q,
                                  "embedder_id": embedder.embedder_id})


def recall_context(conn: psycopg.Connection, key_provider: RootKeyProvider, principal_id: UUID, request: RecallRequest,
                   *, cache: IndexCache, embedder: Embedder, tau_strong_q: int, config_version: str,
                   budget: Budget = Budget(), actor_kind: ActorKind = ActorKind.AGENT, source: Source = Source.CHAT,
                   authorship: Authorship = Authorship.EXTERNAL) -> RecallResult:
    """Recall for `principal_id`; the trace is committed in the issuing stream before the frame is returned."""
    streams = [s for _, s in request.scopes]
    with open_scoped_session(conn, principal_id, snapshot=True) as s:
        snapshot = freeze_snapshot(s, streams)
        frame = build_frame(s, key_provider, snapshot, request, principal_id=principal_id, cache=cache,
                            embedder=embedder, tau_strong_q=tau_strong_q, budget=budget, config_version=config_version)
    with open_scoped_session(conn, principal_id) as w:
        env = record_context_assembled(w, key_provider, issuing_stream=request.issuing_stream, frame=frame,
                                       query_text=request.query, addresses=request.addresses, scopes=request.scopes,
                                       config_version=config_version, actor_id=principal_id, actor_kind=actor_kind,
                                       source=source, authorship=authorship, on_behalf_of=request.on_behalf_of)
    return RecallResult(frame, env.event_id, env.stream_id, env.commit_seq, frame.body["coverage"])
