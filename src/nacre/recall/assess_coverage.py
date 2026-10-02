"""
Functionality: Assess recall coverage (strong / weak / none) from the ranked candidates, deterministically.
Owns: the coverage rule and its threshold parameter.
Public entry: assess_coverage(), Coverage
Decisions: D-0025
Assumptions: A-0034
Notes: D-0025 §6.
  - none: no candidates, or no channel is active, or no active channel has a hit (semantic always "hits"; lexical
    and entity hit when their top score is > 0).
  - strong: the top item is in the top 3 of at least two active channels AND its semantic score >= tau_strong_q
    (cosine * 1e4) AND it is not contested (D1: a contested belief can never make coverage strong; amendment 1 says
    it is never presented as fact).
  - weak: otherwise.
  tau_strong_q has NO default here: it is fixed on the EXP-0004 dev split, recorded as a config_event, and frozen
  before the blind run (owner, D-0025 decision 4). The caller passes the frozen value.
"""
from collections.abc import Mapping, Sequence
from enum import StrEnum

from nacre.recall.rank_candidates import Ranked


class Coverage(StrEnum):
    STRONG = "strong"
    WEAK = "weak"
    NONE = "none"


def _top3(ranked: Sequence[Ranked], attr: str) -> set:
    order = sorted(ranked, key=lambda r: (-getattr(r, attr), str(r.version_event_id)))
    return {r.version_event_id for r in order[:3]}


def assess_coverage(ranked: Sequence[Ranked], active: Mapping[str, bool], *, tau_strong_q: int) -> Coverage:
    if not ranked or not any(active.values()):
        return Coverage.NONE
    hits = {"semantic": True, "lexical": max(r.lexical for r in ranked) > 0, "entity": max(r.entity for r in ranked) > 0}
    if not any(active[c] and hits[c] for c in active):
        return Coverage.NONE
    top = ranked[0]
    agreeing = sum(1 for c in ("semantic", "lexical", "entity") if active.get(c) and top.version_event_id in _top3(ranked, c))
    if agreeing >= 2 and top.semantic >= tau_strong_q and not top.contested:
        return Coverage.STRONG
    return Coverage.WEAK
