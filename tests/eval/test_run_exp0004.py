"""Tests for eval/run_exp0004.py (R25): the command-line guards (tau required and never chosen, live refused while the
instrument is a draft), k = 3 replicates in fresh databases with new run ids (dry run), the results judged only by
the Bar (not applicable on a partial set), the budget cap as a recorded infrastructure abort, and the recorded-replay
identity check. Synthetic data only; the sealed test split is never opened."""
import json
from decimal import Decimal

import pytest

from exp0004_kit import EchoFake, HashEmbedder, env_factory, tiny_split
from nacre.eval import run_exp0004 as R
from nacre.eval.cap_exp0004_budget import BudgetExceeded, CappedProvider
from nacre.eval.grade_exp0004 import SAFETY_METRICS
from nacre.eval.load_exp0004_set import split_views
from nacre.eval.provide_exp0004_models import LiveMode
from nacre.eval.transfer_exp0004 import INSTRUMENT_SHA256
from nacre.recall.load_index_cache import IndexCache


@pytest.fixture
def emb(monkeypatch):
    e = HashEmbedder()
    monkeypatch.setattr("nacre.recall.index_version.default_embedder", lambda: e)
    return e


@pytest.mark.parametrize("argv, message", [
    (["--dry-run"], "--tau is required"),
    (["--dry-run", "--dev-coverage", "--split", "test"], "--dev-coverage runs on --split dev only"),
    (["--dry-run", "--dev-coverage", "--split", "dev", "--tau", "5000"], "takes no --tau"),
])
def test_the_command_line_guards(argv, message, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "set-but-never-read")
    with pytest.raises(SystemExit, match=message):
        R.parse_args(argv)


def test_live_refuses_an_unapproved_instrument(monkeypatch):
    monkeypatch.setattr(R, "INSTRUMENT_APPROVED", False)          # the guard stays, should the text ever change
    monkeypatch.setenv("OPENAI_API_KEY", "set-but-never-read")
    with pytest.raises(SystemExit, match="DRAFT"):
        R.parse_args(["--live", "--tau", "5000"])


def test_live_also_needs_the_key_in_the_environment(monkeypatch):
    monkeypatch.setattr(R, "INSTRUMENT_APPROVED", True)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="OPENAI_API_KEY"):
        R.parse_args(["--live", "--tau", "5000"])


def test_k_is_three_and_tau_is_recorded_never_chosen():
    a = R.parse_args(["--dry-run", "--tau", "6123"])
    assert R.K == 3 and a.tau == 6123 and a.split == "test"


def test_a_dry_run_makes_k_fresh_databases_and_is_not_judged_on_a_partial_set(pg_dsn, test_role_password, tmp_path, emb):
    scopes, grading = split_views(tiny_split())
    a = R.parse_args(["--dry-run", "--tau", "6000", "--split", "dev", "--out", str(tmp_path)])
    out = R.run_cli(a, scopes, grading, [], make_env=env_factory(pg_dsn, test_role_password), embedder=emb)
    (rdir,) = tmp_path.iterdir()
    record = json.loads((rdir / "run.json").read_text())
    assert record["status"] == "complete" and record["tau_strong_q"] == 6000 and record["k"] == 3
    assert record["instrument_sha256"] == INSTRUMENT_SHA256 and record["budget_cap_usd"] == "15"
    assert [r["status"] for r in record["runs"]] == ["complete"] * 3 and len({r["run_id"] for r in record["runs"]}) == 3
    calls = record["dry_meter"]["calls"]
    assert {calls[f"eval.exp0004.transfer.{x}"] for x in "CVN"} == {3 * 8}
    assert Decimal(record["dry_meter"]["worst_case_usd_upper_bound"]) > 0
    assert out["bar"] == {"applicable": False, "checks": out["bar"]["checks"], "PASS": None}
    assert set(out["safety"]) == set(SAFETY_METRICS) and all(v == 0 for v in out["safety"].values())
    assert out["summary"]["N"]["trials"] == 3 * 8 and out["audit"]["dev_test"] == {"shared_scope_ids": 0,
                                                                                   "shared_texts": 0}
    assert out["audit"]["prompt_contains_answer"] == 0 and out["audit"]["prompts_identical_apart_from_memory"]
    assert "bar" not in out["audit"] and "clean" in out["audit"]
    trials = json.loads((rdir / "trials.json").read_text())
    assert len(trials) == 3 * 8 * 3 and json.loads((rdir / "summary.json").read_text())["bar"]["PASS"] is None
    assert (rdir / "fixtures" / "rep3" / "x-s1-02.tasks.jsonl").exists()


def test_the_budget_cap_is_a_recorded_infrastructure_abort_and_nothing_is_retried(pg_dsn, test_role_password, emb):
    scopes, grading = split_views(tiny_split())
    capped = CappedProvider(EchoFake(), Decimal("0.0001"))
    record = {"runs": [], "status": "running"}
    with pytest.raises(BudgetExceeded):
        R.run_experiment(scopes, grading, LiveMode(capped), env_factory(pg_dsn, test_role_password), tau_strong_q=6000, embedder=emb,
                         cache_factory=lambda env: IndexCache(env.key_provider, dim=emb.dim), record=record)
    assert record["status"] == "aborted" and record["abort_reason"] == "budget cap (infrastructure abort)"
    assert [r["status"] for r in record["runs"]] == ["aborted"] and capped.tripped


def test_a_recorded_replay_must_match_the_recorded_instrument_tau_and_split(tmp_path):
    (tmp_path / "run.json").write_text(json.dumps({"instrument_sha256": INSTRUMENT_SHA256, "tau_strong_q": 6000,
                                                   "split": "dev"}))
    scopes, grading = split_views(tiny_split())
    a = R.parse_args(["--recorded", str(tmp_path), "--tau", "6001", "--split", "dev", "--out", str(tmp_path / "o")])
    with pytest.raises(SystemExit, match="not a replay of it"):
        R.run_cli(a, scopes, grading, [])
