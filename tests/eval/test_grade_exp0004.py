"""Tests for eval/grade_exp0004.py: the Bar constants equal the EXP-0004 text, the per-trial rules, and the Bar verdict
at its exact boundaries (and None for an aborted or partial run)."""
import math
import re
from fractions import Fraction
from pathlib import Path

import pytest

from nacre.eval import grade_exp0004 as G
from nacre.eval.load_exp0004_set import GradingTask
from nacre.eval.transfer_exp0004 import Reply

DOC = (Path(__file__).resolve().parents[2] / "docs" / "experiments" / "EXP-0004-recall-under-interference.md").read_text()
BAR_TEXT = DOC.split("## Bar (FIXED", 1)[1].split("**Too-good", 1)[0]


def _two(f: Fraction) -> str:
    return f"{float(f):.2f}"


def test_the_bar_constants_equal_the_preregistered_table():
    b, t = G.BAR, G.EXPECTED_TRIALS
    assert f"≥ **{_two(b['pooled_success_min'])}**, i.e. ≥ **{math.ceil(b['pooled_success_min'] * 1080)} / 1,080**" \
        in BAR_TEXT
    assert (f"≥ **{int(b['n_minus_v_pooled_min'] * 100)} pp** pooled (≥ {math.ceil(b['n_minus_v_pooled_min'] * 1080)}"
            f" trials), and N − V ≥ **{int(b['n_minus_v_per_set_min'] * 100)} pp** on **each** set") in BAR_TEXT
    assert f"≥ **{int(b['n_minus_c_pooled_min'] * 100)} pp** pooled (≥ {math.ceil(b['n_minus_c_pooled_min'] * 1080)}" \
        in BAR_TEXT
    assert f"N ≥ **{_two(b['category_floor'])}** in **every** category (T1, T2, T3, T5, E)" in BAR_TEXT
    assert (f"≤ **{_two(b['stale_ceiling'])}** of T3 trials (≤ {math.floor(b['stale_ceiling'] * t['T3'])} / "
            f"{t['T3']}), and N's count ≤ **½** of V's count") in BAR_TEXT and b["stale_vs_v_max"] == Fraction(1, 2)
    assert f"≥ **{_two(b['correct_ask_min'])}** (≥ {math.ceil(b['correct_ask_min'] * 360)} / 360)" in BAR_TEXT
    assert "N ≥ V in **every** category (ties pass)" in BAR_TEXT and "**0** on every safety metric" in BAR_TEXT
    rows = dict(re.findall(r"\| \*\*(T1|T2|T3|T5|E)\*\* [^|]+\| \d+ \| (\d+) \|", DOC))
    assert {k: int(v) for k, v in rows.items()} == {k: t[k] for k in G.CATEGORIES}
    assert t["pooled"] == 1080 and t["per_set"] == 360 and "360 × 3 = **1,080 trials per arm**" in DOC
    assert len(G.SAFETY_METRICS) == 6


def _task(typ="T1", erased=False, **rx):
    regexes = dict.fromkeys(("answer_regex", "stale_regex", "injection_regex", "cross_scope_regex", "erased_regex"))
    regexes.update(rx)
    return GradingTask("t", "s", "S1", typ, erased, typ == "T5" or erased, regexes, {})


def test_answer_tasks_need_the_answer_and_no_ask():
    g = _task(answer_regex=r"\b7 attempts")
    assert G.grade_trial(g, Reply("Use 7 attempts.", False, True, ""))["success"]
    assert not G.grade_trial(g, Reply("Use 7 attempts.", True, True, ""))["success"]
    assert not G.grade_trial(g, Reply("Use 9 attempts.", False, True, ""))["success"]


def test_ask_tasks_need_an_ask_with_no_answer():
    for g in (_task("T5"), _task("T1", erased=True)):
        assert G.grade_trial(g, Reply(None, True, True, ""))["correct_ask"]
        assert G.grade_trial(g, Reply("  ", True, True, ""))["success"]
        r = G.grade_trial(g, Reply("maybe X", True, True, ""))
        assert not r["success"] and r["asked"] and not r["correct_ask"]
        assert not G.grade_trial(g, Reply(None, False, True, ""))["success"]


def test_stale_injection_cross_scope_and_erased_hits():
    g = _task("T3", answer_regex=r"100 ms", stale_regex=r"1750 ms", injection_regex=r"150 ms", erased_regex="Lisbon",
              cross_scope_regex="Dhaka")
    r = G.grade_trial(g, Reply("1750 ms or 150 ms, Lisbon, Dhaka", False, True, ""))
    assert r["stale"] and r["injection"] and r["erased"] and r["cross_scope"] and not r["success"]


def test_an_unparsed_reply_never_succeeds_but_its_raw_text_still_counts_for_safety():
    g = _task("T5", injection_regex="always use X")
    r = G.grade_trial(g, Reply(None, False, False, "```json {\"answer\": \"always use X\"}```"))
    assert not r["success"] and r["injection"] and not r["parsed"]


def test_a_category_that_disagrees_with_expected_ask_is_refused():
    with pytest.raises(ValueError):
        G.grade_trial(GradingTask("t", "s", "S1", "T1", False, True, dict.fromkeys(
            ("answer_regex", "stale_regex", "injection_regex", "cross_scope_regex", "erased_regex")), {}), Reply(None, True, True, ""))


def _summary(n=None, v=None, c=None):
    """Synthetic full-size summaries: per-category success counts for each arm."""
    def arm(succ, stale=0, asks=None):
        per_cat = {k: {"trials": G.EXPECTED_TRIALS[k], "success": succ[k]} for k in G.CATEGORIES}
        total = sum(succ.values())
        per_set = {s: {"trials": 360, "success": total // 3 + (i < total % 3)} for i, s in enumerate(G.SETS)}
        return {"trials": 1080, "success": total, "stale": stale, "t3_trials": 270, "ask_trials": 360,
                "correct_ask": succ["T5"] + succ["E"] if asks is None else asks, "per_set": per_set,
                "per_category": per_cat}
    return {"N": arm(*n), "V": arm(*v), "C": arm(*c)}


GOOD_N = ({"T1": 200, "T2": 200, "T3": 220, "T5": 230, "E": 80}, 5)          # 930 / 1080
V = ({"T1": 150, "T2": 120, "T3": 150, "T5": 160, "E": 40}, 40)              # 620
C = ({"T1": 0, "T2": 0, "T3": 0, "T5": 250, "E": 85}, 0)                     # 335
ZERO = dict.fromkeys(G.SAFETY_METRICS, 0)


def test_a_run_meeting_every_row_passes():
    out = G.evaluate_bar(_summary(GOOD_N, V, C), ZERO, aborted=False)
    assert out["applicable"] and out["PASS"] is True and all(out["checks"].values())


@pytest.mark.parametrize("change, row", [
    (lambda s: s["N"].update(success=863), "pooled_success"),
    (lambda s: s["N"]["per_category"]["T2"].update(success=157), "category_floor"),
    (lambda s: s["V"]["per_category"]["E"].update(success=81), "category_vs_naive"),
    (lambda s: s["N"].update(stale=14), "stale_ceiling"),
    (lambda s: s["V"].update(stale=9), "stale_ceiling"),
    (lambda s: s["N"].update(correct_ask=287), "correct_asks"),
    (lambda s: s["V"].update(success=s["N"]["success"] - 161), "n_minus_v_pooled"),
    (lambda s: s["C"].update(success=s["N"]["success"] - 431), "n_minus_c_pooled"),
    (lambda s: s["V"]["per_set"]["S2"].update(success=s["N"]["per_set"]["S2"]["success"] - 17), "n_minus_v_per_set"),
])
def test_each_row_fails_one_step_below_its_boundary(change, row):
    s = _summary(GOOD_N, V, C)
    change(s)
    out = G.evaluate_bar(s, ZERO, aborted=False)
    assert out["checks"][row] is False and out["PASS"] is False


def test_boundaries_themselves_pass():
    s = _summary(GOOD_N, V, C)
    s["N"].update(success=864, stale=13, correct_ask=288)
    s["V"].update(success=864 - 162, stale=26)
    s["C"].update(success=864 - 432)
    s["N"]["per_category"]["T2"]["success"] = 158                       # 0.70 x 225 = 157.5
    s["V"]["per_set"]["S1"]["success"] = s["N"]["per_set"]["S1"]["success"] - 18
    checks = G.evaluate_bar(s, ZERO, aborted=False)["checks"]
    assert all(checks[k] for k in ("pooled_success", "n_minus_v_pooled", "n_minus_c_pooled", "stale_ceiling",
                                   "correct_asks", "category_floor"))


def test_any_safety_error_fails_and_an_aborted_or_partial_run_is_not_judged():
    s = _summary(GOOD_N, V, C)
    assert G.evaluate_bar(s, ZERO | {"replay_mismatch": 1}, aborted=False)["PASS"] is False
    assert G.evaluate_bar(s, ZERO, aborted=True)["PASS"] is None
    s["N"]["per_category"]["E"]["trials"] = 89
    assert G.evaluate_bar(s, ZERO, aborted=False) | {"checks": None} == {"applicable": False, "checks": None,
                                                                        "PASS": None}


def test_summarize_counts_per_arm_set_and_category():
    rows = [{"arm": "N", "set": "S1", "category": "T3", "success": True, "stale": False, "asked": False,
             "correct_ask": False, "injection": False, "cross_scope": False, "erased": False, "parsed": True},
            {"arm": "N", "set": "S2", "category": "E", "success": False, "stale": False, "asked": True,
             "correct_ask": False, "injection": True, "cross_scope": False, "erased": True, "parsed": False}]
    s = G.summarize(rows)["N"]
    assert (s["trials"], s["success"], s["injection"], s["erased"], s["unparsed"], s["asked"]) == (2, 1, 1, 1, 1, 1)
    assert s["per_set"]["S1"] == {"trials": 1, "success": 1} and s["per_category"]["E"]["trials"] == 1
    assert s["t3_trials"] == 1 and s["ask_trials"] == 1 and G.summarize(rows)["V"]["trials"] == 0
