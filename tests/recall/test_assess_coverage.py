"""Tests for recall/assess_coverage.py (R17, D-0025 §6): none / weak / strong, deterministic, and a contested top item
never yields strong coverage."""
import uuid
from fractions import Fraction

from nacre.recall.assess_coverage import Coverage, assess_coverage
from nacre.recall.rank_candidates import Ranked


def _r(i, semantic=0, lexical=0, entity=0, contested=False):
    return Ranked(uuid.UUID(int=i), contested, 0, 1, semantic, lexical, entity, Fraction(0), 0)


ALL = {"semantic": True, "lexical": True, "entity": True}


def test_none_when_empty_or_no_active_channel_or_no_hit():
    assert assess_coverage([], ALL, tau_strong_q=5000) == Coverage.NONE
    assert assess_coverage([_r(1, 9000)], dict.fromkeys(ALL, False), tau_strong_q=5000) == Coverage.NONE
    assert assess_coverage([_r(1), _r(2)], {"semantic": False, "lexical": True, "entity": True},
                           tau_strong_q=5000) == Coverage.NONE           # active but nothing scored


def test_strong_needs_two_agreeing_channels_and_the_threshold():
    ranked = [_r(1, 9000, 500, 2), _r(2, 4000, 0, 0), _r(3, 1000, 0, 0), _r(4, 900, 0, 0)]
    assert assess_coverage(ranked, ALL, tau_strong_q=8000) == Coverage.STRONG
    assert assess_coverage(ranked, ALL, tau_strong_q=9500) == Coverage.WEAK             # below the threshold
    only_sem = {"semantic": True, "lexical": False, "entity": False}
    assert assess_coverage(ranked, only_sem, tau_strong_q=8000) == Coverage.WEAK         # one channel agrees


def test_a_contested_top_item_is_never_strong():
    ranked = [_r(1, 9000, 500, 2, contested=True), _r(2, 100), _r(3, 50)]
    assert assess_coverage(ranked, ALL, tau_strong_q=1000) == Coverage.WEAK
