"""Tests for eval/select_exp0004_tau.py: the balanced-accuracy maths on hand-built rows, ties to the higher tau, the
loud failures (all / none answerable, nothing strong-eligible, malformed rows), the probe reduction proved equal to
assess_coverage at tau over generated rankings (through the real frame fill), the frozen record and every refusal
of check_frozen, the config_event, and the select-tau command line. Dev data and synthetic data only; the sealed
test split is never opened."""
import hashlib
import json
import sys
import uuid
from fractions import Fraction
from types import SimpleNamespace
from unittest import mock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from nacre.eval import select_exp0004_tau as S
from nacre.eval.load_exp0004_set import DEV_SHA256
from nacre.eval.run_exp0004_replicate import DEV_COVERAGE_BUDGET, N_BUDGET, TAU_PROBE
from nacre.recall import assemble_frame as AF
from nacre.recall.assess_coverage import Coverage, assess_coverage
from nacre.recall.rank_candidates import Ranked


def row(task, category, coverage, top, rep=1):
    return {"rep": rep, "task_id": task, "category": category, "answerable": category in ("T1", "T2", "T3"),
            "coverage": coverage, "top_semantic": top, "items": 1}


# ---------------------------------------------------------------- selection maths

def test_balanced_accuracy_on_hand_built_rows():
    rows = [row("a1", "T1", "strong", 8000), row("a2", "T2", "strong", 6000), row("a3", "T3", "weak", 9000),
            row("a4", "T1", "none", None),
            row("u1", "T5", "strong", 7000), row("u2", "E", "strong", 5000), row("u3", "T5", "weak", 9500)]
    # pos = 4, neg = 3. tau 8000: TP 1, FP 0 -> (1/4 + 3/3)/2 = 5/8. tau 7000: TP 1, FP 1 -> (1/4 + 2/3)/2 = 11/24.
    # tau 6000: TP 2, FP 1 -> (2/4 + 2/3)/2 = 7/12. tau 5000: TP 2, FP 2 -> (2/4 + 1/3)/2 = 5/12.
    s = S.select_tau(rows)
    assert (s.tau_strong_q, s.balanced_accuracy) == (8000, Fraction(5, 8))
    assert (s.tp, s.fn, s.tn, s.fp) == (1, 3, 3, 0)
    assert (s.candidates, s.rows, s.strong_eligible) == (4, 7, 4)


def test_a_tie_goes_to_the_higher_tau():
    rows = [row("a1", "T1", "strong", 9000), row("u1", "T5", "strong", 8000), row("a2", "T2", "strong", 7000),
            row("u2", "E", "weak", 1)]
    # tau 9000: (1/2 + 2/2)/2 = 3/4. tau 8000: (1/2 + 1/2)/2 = 1/2. tau 7000: (2/2 + 1/2)/2 = 3/4 -> tie, 9000 wins.
    s = S.select_tau(rows)
    assert s.tau_strong_q == 9000 and s.balanced_accuracy == Fraction(3, 4)
    assert S.select_tau(list(reversed(rows))) == s                          # deterministic, order-free


def test_pooled_replicates_are_separate_observations():
    rows = [row("a", "T1", "strong", 6000, rep) for rep in (1, 2, 3)] + [row("u", "T5", "strong", 5000, rep)
                                                                          for rep in (1, 2, 3)]
    s = S.select_tau(rows)
    assert (s.tau_strong_q, s.balanced_accuracy, s.rows) == (6000, Fraction(1), 6)


@pytest.mark.parametrize("rows, message", [
    ([row("a", "T1", "strong", 5000), row("b", "T2", "weak", 1)], "0 unanswerable"),
    ([row("u", "T5", "strong", 5000), row("e", "E", "none", None)], "0 answerable"),
    ([], "0 answerable"),
    ([row("a", "T1", "weak", 5000), row("u", "T5", "none", None)], "no strong-eligible"),
    ([row("a", "T1", "strong", None), row("u", "T5", "weak", 1)], "integer top_semantic"),
    ([row("a", "T1", "strong", 5000.0), row("u", "T5", "weak", 1)], "integer top_semantic"),
    ([row("a", "T1", "strong", 10001), row("u", "T5", "weak", 1)], "integer top_semantic"),
    ([row("a", "T4", "strong", 5000), row("u", "T5", "weak", 1)], "known category"),
    ([row("a", "T1", "maybe", 5000), row("u", "T5", "weak", 1)], "coverage"),
    ([row("a", "T1", "strong", 5000), row("a", "T1", "weak", 1), row("u", "T5", "weak", 1)], "duplicate"),
    ([row("a", "T1", "strong", 5000) | {"answerable": False}, row("u", "T5", "weak", 1)], "disagrees"),
    ([row("a", "T1", "strong", 5000), row("e", "E", "weak", 1) | {"answerable": True}], "disagrees"),
])
def test_it_fails_loudly_and_never_defaults(rows, message):
    with pytest.raises(S.TauSelectionError, match=message):
        S.select_tau(rows)


# ---------------------------------------------------------------- the probe reduction is exact

_RANKED = st.lists(st.tuples(st.integers(-10_000, 10_000), st.integers(0, 4), st.integers(0, 2), st.booleans(),
                             st.integers(0, 6000)), min_size=0, max_size=7)
_ACTIVE = st.fixed_dictionaries({c: st.booleans() for c in ("semantic", "lexical", "entity")})


def _frame_top(ranked, active, texts, budget):
    """items[0]'s semantic score from the REAL assemble_frame fill (frame_item stubbed: it only reads the ledger)."""
    stub = lambda session, kp, c, text, r: {"version_event_id": str(r.version_event_id),  # noqa: E731
                                            "scores": {"semantic": r.semantic}}
    with mock.patch.object(AF, "frame_item", stub):
        f = AF.assemble_frame(None, None, snapshot=SimpleNamespace(canonical=lambda: {}), scopes=[],
                              principal_id=uuid.uuid4(), query_text="q", addresses=[], relaxations=[],
                              candidates=[SimpleNamespace(version_event_id=r.version_event_id) for r in ranked],
                              ranked=ranked, active=active, texts=texts, coverage="x", budget=budget)
    items = f.body["items"]
    return items[0]["scores"]["semantic"] if items else None


def _case(spec, active):
    ranked = [Ranked(uuid.UUID(int=i + 1), contested, 0, i, sem, lex, ent, Fraction(0), 0)
              for i, (sem, lex, ent, contested, _) in enumerate(spec)]
    return ranked, active, {r.version_event_id: "x" * spec[i][4] for i, r in enumerate(ranked)}


@settings(max_examples=400, deadline=None)
@given(_RANKED, _ACTIVE, st.data())
def test_probe_reduction_equals_assess_coverage_at_every_tau(spec, active, data):
    ranked, active, texts = _case(spec, active)
    probe = assess_coverage(ranked, active, tau_strong_q=TAU_PROBE)
    dev_row = {"category": "T1", "answerable": True, "coverage": probe.value,
               "top_semantic": _frame_top(ranked, active, texts, DEV_COVERAGE_BUDGET)}
    taus = [TAU_PROBE, -10_000, 10_000, 10_001] + [r.semantic + d for r in ranked for d in (-1, 0, 1)]
    for tau in taus + [data.draw(st.integers(-10_001, 10_001))]:
        direct = assess_coverage(ranked, active, tau_strong_q=tau) == Coverage.STRONG
        assert S.strong_at(dev_row, tau) is direct, (tau, dev_row)


def test_under_the_n_budget_the_reduction_is_not_exact_which_is_why_dev_has_no_char_limit():
    spec = [(5000, 3, 1, False, 4001), (9000, 2, 1, False, 10)]       # the ranked top's text exceeds 4,000 chars
    ranked, active, texts = _case(spec, {"semantic": True, "lexical": True, "entity": True})
    assert assess_coverage(ranked, active, tau_strong_q=TAU_PROBE) == Coverage.STRONG
    assert _frame_top(ranked, active, texts, N_BUDGET) == 9000            # items[0] is NOT the ranked top
    assert _frame_top(ranked, active, texts, DEV_COVERAGE_BUDGET) == 5000
    assert assess_coverage(ranked, active, tau_strong_q=8000) == Coverage.WEAK
    assert DEV_COVERAGE_BUDGET.items == N_BUDGET.items and DEV_COVERAGE_BUDGET.chars == sys.maxsize


# ---------------------------------------------------------------- the frozen record and its refusals

ROWS = [row("a1", "T1", "strong", 6000), row("a2", "T2", "weak", 7000), row("u1", "T5", "strong", 5000),
        row("u2", "E", "none", None)]
DEV_RUN = {"folder": "EXP-0004-x-LIVE", "mode": "live", "k": 1, "run_ids": ["r1"], "embedder_id": "e",
           "coverage_rows_sha256": "0" * 64}


def write_record(path, **over):
    rec = S.make_record(ROWS, dev_run=over.pop("dev_run", DEV_RUN), split_sha256=DEV_SHA256, date="2026-10-02")
    path.write_text(json.dumps(rec | over, indent=1, sort_keys=True))
    return rec


def test_a_valid_record_is_accepted_and_carries_its_own_sha(tmp_path):
    rec = write_record(tmp_path / "TAU.json")
    assert rec["tau_strong_q"] == 6000 and rec["selection"]["balanced_accuracy"] == "3/4"
    got = S.check_frozen(tmp_path / "TAU.json", tau=6000, live=True)
    assert got["record_sha256"] == hashlib.sha256((tmp_path / "TAU.json").read_bytes()).hexdigest()


@pytest.mark.parametrize("over, tau, live, message", [
    ({}, 6001, False, "does not match the frozen"),
    ({"dev_split_sha256": "f" * 64}, 6000, False, "another dev split"),
    ({"selection_code_sha256": "0" * 64}, 6000, False, "selection code changed"),
    ({"tau_strong_q": 5000}, 5000, False, "does not match its own rows"),
    ({"selection": {"tau_strong_q": 6000}}, 6000, False, "does not match its own rows"),
    ({"rows": [row("a1", "T1", "strong", 6000)]}, 6000, False, "does not reselect"),
    ({"dev_run": DEV_RUN | {"mode": "dry"}}, 6000, True, "not a dry one"),
])
def test_check_frozen_refuses(tmp_path, over, tau, live, message):
    write_record(tmp_path / "TAU.json", **over)
    with pytest.raises(SystemExit, match=message):
        S.check_frozen(tmp_path / "TAU.json", tau=tau, live=live)


def test_a_dry_dev_record_is_enough_for_plumbing_runs(tmp_path):
    write_record(tmp_path / "TAU.json", dev_run=DEV_RUN | {"mode": "dry"})
    assert S.check_frozen(tmp_path / "TAU.json", tau=6000, live=False)["tau_strong_q"] == 6000


def test_check_frozen_refuses_a_missing_record_and_a_changed_dev_split(tmp_path):
    with pytest.raises(SystemExit, match="tau is not frozen"):
        S.check_frozen(tmp_path / "TAU.json", tau=6000, live=False)
    write_record(tmp_path / "TAU.json")
    (tmp_path / "dev.json").write_text("{}")
    with pytest.raises(SystemExit, match="dev.json on disk"):
        S.check_frozen(tmp_path / "TAU.json", tau=6000, live=False, dev_path=tmp_path / "dev.json")


def _run_dir(tmp_path, rows, **run):
    d = tmp_path / "EXP-0004-run-LIVE"
    d.mkdir()
    (d / "run.json").write_text(json.dumps({"experiment": "EXP-0004", "split": "dev", "dev_coverage": True,
                                            "status": "complete", "mode": "live", "k": 1, "split_sha256": DEV_SHA256,
                                            "embedder_id": "e", "runs": [{"run_id": "r1", "status": "complete"}]}
                                           | run))
    (d / "coverage_rows.json").write_text(json.dumps(rows))
    return d


GRADING = {t: SimpleNamespace(category=c) for t, c in (("a1", "T1"), ("a2", "T2"), ("u1", "T5"), ("u2", "E"))}


def test_build_record_needs_a_complete_dev_run_with_one_row_per_task_and_replicate(tmp_path):
    rec = S.build_record(_run_dir(tmp_path, ROWS), GRADING, date="2026-10-02")
    assert rec["tau_strong_q"] == 6000 and rec["dev_run"]["run_ids"] == ["r1"] and rec["rows"] == ROWS
    for i, (rows, run, message) in enumerate([
            (ROWS[:3], {}, "one per dev task"),
            (ROWS, {"status": "aborted"}, "not a complete"),
            (ROWS, {"dev_coverage": False}, "not a complete"),
            (ROWS, {"split_sha256": "f" * 64}, "run on split"),
            ([r | {"category": "T2"} if r["task_id"] == "a1" else r for r in ROWS], {}, "grading view")]):
        sub = tmp_path / str(i)
        sub.mkdir()
        with pytest.raises(S.TauSelectionError, match=message):
            S.build_record(_run_dir(sub, rows, **run), GRADING)


def test_the_exp_doc_block_names_tau_the_pins_and_the_counts():
    rec = S.make_record(ROWS, dev_run=DEV_RUN, split_sha256=DEV_SHA256, date="2026-10-02")
    block = S.exp_doc_block(rec, "ab" * 32)
    assert "**tau_strong_q = 6000**" in block and "3/4 = 0.7500" in block and "TP 1, FN 1, TN 2, FP 0" in block
    assert DEV_SHA256 in block and "ab" * 32 in block and rec["selection_code_sha256"] in block


def test_the_cli_writes_the_record_once_and_never_overwrites(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "load_split", lambda path, sha: ([], GRADING) if path.name == "dev.json" else 1 / 0)
    d = _run_dir(tmp_path, ROWS)
    rec = S.main([str(d), "--record", str(tmp_path / "TAU.json")])
    assert json.loads((tmp_path / "TAU.json").read_text()) == rec and (d / "tau_selection.md").exists()
    assert S.check_frozen(tmp_path / "TAU.json", tau=rec["tau_strong_q"], live=True)
    with pytest.raises(SystemExit, match="never overwritten"):
        S.main([str(d), "--record", str(tmp_path / "TAU.json")])


def test_the_cli_fails_loudly_without_writing_when_nothing_is_strong(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "load_split", lambda path, sha: ([], GRADING))
    d = _run_dir(tmp_path, [r | {"coverage": "weak"} for r in ROWS])
    with pytest.raises(SystemExit, match="no tau selected: no strong-eligible"):
        S.main([str(d), "--record", str(tmp_path / "TAU.json")])
    assert not (tmp_path / "TAU.json").exists()


def test_config_event_content_has_no_floats_and_rejects_other_ops():
    rec = S.make_record(ROWS, dev_run=DEV_RUN, split_sha256=DEV_SHA256, date="2026-10-02") | {"record_sha256": "c"}
    c = S.config_event_content(rec)
    assert c["op"] == "recall_tau" and c["tau_strong_q"] == 6000 and c["balanced_accuracy"] == "3/4"
    assert not any(isinstance(v, float) for v in c.values())
    with pytest.raises(S.TauSelectionError):
        S.append_tau_config_event(None, None, org_id=uuid.uuid4(), content=c | {"op": "x"}, idempotency_key="k")
