"""
Functionality: Rank recall candidates with three channels (semantic, lexical, entity), channel abstention and
  deterministic reciprocal-rank fusion, contested candidates always below uncontested ones.
Owns: the tokeniser (NFKC, casefold, split on non-alphanumerics), BM25 over the pool, quantised cosine, the abstention
  rules, RRF in exact rational arithmetic, and the total tie order.
Public entry: rank_candidates(), Ranked, tokens()
Decisions: D-0025, D-0024
Assumptions: A-0034, A-0036
Notes: D-0025 §4 and amendment 1 (owner, 2026-10-02).
  - semantic: cosine of the query embedding with the entry embedding, quantised to round(cos * 10^4) (half-even), so
    rankings never depend on BLAS summation order beyond 1e-4 (A-0036). Abstains when max - median < 500 (0.05).
  - lexical: BM25 (k1 = 1.2, b = 0.75) of the query tokens over each candidate's index text, quantised to 10^-4.
    Abstains when no candidate scores above 0.
  - entity: the exact request-address matches from narrow_by_identity. Abstains when there are no request addresses
    or every candidate ties.
  - fusion: RRF over the non-abstaining channels, sum 1/(60 + rank) with rank from 1 in each channel's order (score
    desc, then version_event_id), as an exact Fraction; `fused_q` = floor(fused * 10^8) for the frame.
  - total order: UNCONTESTED BEFORE CONTESTED (amendment 1: contested beliefs always rank below uncontested), then
    fused desc, then level_rank asc (narrower scope), then commit_seq desc (newer), then version_event_id asc.
  Pure function over already-decrypted entries: no database, no keys.
"""
import math
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from uuid import UUID

import numpy as np

K1, B, RRF_K = 1.2, 0.75, 60
SEMANTIC_ABSTAIN_Q = 500             # 0.05 in units of 1e-4


@dataclass(frozen=True)
class Ranked:
    version_event_id: UUID
    contested: bool
    level_rank: int
    commit_seq: int
    semantic: int                     # cosine * 1e4
    lexical: int                      # BM25 * 1e4
    entity: int
    fused: Fraction
    fused_q: int                      # floor(fused * 1e8)


def tokens(text: str) -> list[str]:
    norm = unicodedata.normalize("NFKC", text).casefold()
    out, cur = [], []
    for ch in norm:
        if ch.isalnum():
            cur.append(ch)
        elif cur:
            out.append("".join(cur))
            cur = []
    if cur:
        out.append("".join(cur))
    return out


def _bm25(query: list[str], docs: Mapping[UUID, list[str]]) -> dict[UUID, int]:
    n = len(docs)
    if not n or not query:
        return {v: 0 for v in docs}
    avg = sum(len(d) for d in docs.values()) / n or 1.0
    df = Counter(t for d in docs.values() for t in set(d))
    out = {}
    for v, d in docs.items():
        tf, score = Counter(d), 0.0
        for t in set(query):
            if tf[t]:
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                score += idf * tf[t] * (K1 + 1) / (tf[t] + K1 * (1 - B + B * len(d) / avg))
        out[v] = round(score * 10_000)
    return out


def _channel_ranks(scores: Mapping[UUID, int]) -> dict[UUID, int]:
    order = sorted(scores, key=lambda v: (-scores[v], str(v)))
    return {v: i + 1 for i, v in enumerate(order)}


def rank_candidates(candidates: Sequence, texts: Mapping[UUID, str], embeddings: Mapping[UUID, np.ndarray],
                    query_text: str, query_embedding: np.ndarray, exact_matches: Mapping[UUID, int],
                    has_request_addresses: bool) -> tuple[list[Ranked], dict[str, bool]]:
    """Ranked candidates (total order) and which channels were active. `candidates` carry version_event_id, status,
    level_rank and commit_seq (merge_scopes.Candidate)."""
    ids = [c.version_event_id for c in candidates]
    q = np.asarray(query_embedding, dtype=np.float32)
    semantic = {v: round(float(np.dot(embeddings[v], q)) * 10_000) for v in ids}
    lexical = _bm25(tokens(query_text), {v: tokens(texts.get(v, "")) for v in ids})
    entity = {v: int(exact_matches.get(v, 0)) for v in ids}
    sem_vals = sorted(semantic.values())
    active = {
        "semantic": bool(ids) and (sem_vals[-1] - sem_vals[len(sem_vals) // 2]) >= SEMANTIC_ABSTAIN_Q,
        "lexical": any(s > 0 for s in lexical.values()),
        "entity": has_request_addresses and len(set(entity.values())) > 1,
    }
    channel = {"semantic": semantic, "lexical": lexical, "entity": entity}
    ranks = {name: _channel_ranks(channel[name]) for name, on in active.items() if on}
    by_id = {c.version_event_id: c for c in candidates}
    out = []
    for v in ids:
        fused = sum((Fraction(1, RRF_K + r[v]) for r in ranks.values()), Fraction(0))
        c = by_id[v]
        out.append(Ranked(v, c.status == "contested", c.level_rank, c.commit_seq, semantic[v], lexical[v], entity[v],
                          fused, math.floor(fused * 10**8)))
    out.sort(key=lambda r: (r.contested, -r.fused, r.level_rank, -r.commit_seq, str(r.version_event_id)))
    return out, active
