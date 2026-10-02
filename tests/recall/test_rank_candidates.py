"""Tests for recall/rank_candidates.py (R15, D-0025 §4 + amendment 1): channel scores and abstention, deterministic RRF,
contested candidates always ranked below uncontested ones, and the total tie order."""
import uuid
from dataclasses import dataclass
from fractions import Fraction

import numpy as np

from nacre.recall.rank_candidates import rank_candidates, tokens


@dataclass(frozen=True)
class C:
    version_event_id: uuid.UUID
    status: str = "active"
    level_rank: int = 0
    commit_seq: int = 1


def _unit(*xs):
    v = np.array(xs + (0.0,) * (384 - len(xs)), dtype=np.float32)
    return v / np.linalg.norm(v)


def _setup(n=4):
    ids = [uuid.UUID(int=i + 1) for i in range(n)]
    return ids, [C(v) for v in ids]


def test_tokens_are_nfkc_casefolded_alphanumeric_runs():
    assert tokens("Pin  the ＡＰＩ-key, v2!") == ["pin", "the", "api", "key", "v2"]


def test_semantic_channel_ranks_the_closest_first():
    ids, cands = _setup(3)
    emb = {ids[0]: _unit(1, 0), ids[1]: _unit(0.6, 0.8), ids[2]: _unit(0, 1)}
    ranked, active = rank_candidates(cands, {}, emb, "", _unit(1, 0), {}, False)
    assert active == {"semantic": True, "lexical": False, "entity": False}
    assert [r.version_event_id for r in ranked] == ids
    assert ranked[0].semantic == 10_000 and ranked[0].fused == Fraction(1, 61)


def test_a_channel_that_cannot_discriminate_abstains():
    ids, cands = _setup(3)
    emb = {v: _unit(1, 0) for v in ids}                                  # identical: max - median = 0
    texts = {v: "nothing in common" for v in ids}
    ranked, active = rank_candidates(cands, texts, emb, "payments retry", _unit(1, 0), {}, False)
    assert active == {"semantic": False, "lexical": False, "entity": False}
    assert all(r.fused == 0 for r in ranked)


def test_lexical_and_entity_channels_fuse_by_reciprocal_rank():
    ids, cands = _setup(3)
    emb = {v: _unit(1, 0) for v in ids}
    texts = {ids[0]: "retry payments", ids[1]: "unrelated words", ids[2]: "payments only"}
    ranked, active = rank_candidates(cands, texts, emb, "retry payments", _unit(1, 0),
                                     {ids[1]: 2, ids[2]: 1}, True)
    assert active == {"semantic": False, "lexical": True, "entity": True}
    fused = {r.version_event_id: r.fused for r in ranked}
    assert fused[ids[0]] == Fraction(1, 61) + Fraction(1, 63)            # lexical 1st, entity 3rd
    assert fused[ids[1]] == Fraction(1, 63) + Fraction(1, 61)            # lexical 3rd, entity 1st
    assert fused[ids[2]] == Fraction(1, 62) + Fraction(1, 62)


def test_contested_always_ranks_below_uncontested_even_with_a_higher_score():
    # D-0025 amendment 1 (owner): contested beliefs are ranked below uncontested ones.
    ids, _ = _setup(3)
    cands = [C(ids[0], status="contested"), C(ids[1]), C(ids[2])]
    emb = {ids[0]: _unit(1, 0), ids[1]: _unit(0.3, 0.95), ids[2]: _unit(0, 1)}
    ranked, _ = rank_candidates(cands, {}, emb, "", _unit(1, 0), {}, False)
    assert ranked[0].semantic < ranked[-1].semantic                     # the best match is the contested one...
    assert [r.version_event_id for r in ranked][-1] == ids[0] and ranked[-1].contested   # ...and it is last
    assert not any(r.contested for r in ranked[:-1])


def test_ties_break_by_scope_then_recency_then_id():
    ids, _ = _setup(3)
    cands = [C(ids[0], level_rank=1, commit_seq=9), C(ids[1], level_rank=0, commit_seq=1),
             C(ids[2], level_rank=0, commit_seq=5)]
    emb = {v: _unit(1, 0) for v in ids}
    ranked, _ = rank_candidates(cands, {}, emb, "", _unit(1, 0), {}, False)
    assert [r.version_event_id for r in ranked] == [ids[2], ids[1], ids[0]]


def test_ranking_is_deterministic_under_tiny_float_noise():
    ids, cands = _setup(4)
    rng = np.random.default_rng(1)
    base = {v: _unit(*rng.random(8)) for v in ids}
    noisy = {v: (e + np.float32(1e-7)) / np.linalg.norm(e + np.float32(1e-7)) for v, e in base.items()}
    q = _unit(*rng.random(8))
    a, _ = rank_candidates(cands, {}, base, "", q, {}, False)
    b, _ = rank_candidates(cands, {}, noisy, "", q, {}, False)
    assert [r.version_event_id for r in a] == [r.version_event_id for r in b]
