"""Tests for eval/run_exp0004.py (R25): the command-line guards (tau required and never chosen, live refused while the
instrument is a draft), k = 3 replicates in fresh databases with new run ids (dry run), the results judged only by
the Bar (not applicable on a partial set), the budget cap as a recorded infrastructure abort, and the recorded-replay
identity check. Synthetic data only; the sealed test split is never opened."""
import hashlib
import json
from decimal import Decimal

import pytest

from exp0004_kit import EchoFake, HashEmbedder, env_factory, tiny_split
from nacre.eval import run_exp0004 as R
from nacre.eval.cap_exp0004_budget import BudgetExceeded, CappedProvider
from nacre.eval.grade_exp0004 import SAFETY_METRICS
from nacre.eval.load_exp0004_set import DEV_SHA256, split_views
from nacre.eval.select_exp0004_tau import make_record
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


def _frozen(tmp_path, tau=6000, mode="dry"):
    """A frozen record (selected from hand-built rows) whose tau is `tau`."""
    rows = [{"rep": 1, "task_id": "a", "category": "T1", "answerable": True, "coverage": "strong", "top_semantic": tau},
            {"rep": 1, "task_id": "u", "category": "T5", "answerable": False, "coverage": "weak", "top_semantic": 1}]
    rec = make_record(rows, dev_run={"folder": "f", "mode": mode, "k": 1, "run_ids": ["r"]}, split_sha256=DEV_SHA256,
                      date="2026-10-02")
    path = tmp_path / "TAU.json"
    path.write_text(json.dumps(rec))
    return path


def test_a_dry_run_makes_k_fresh_databases_and_is_not_judged_on_a_partial_set(pg_dsn, test_role_password, tmp_path, emb):
    scopes, grading = split_views(tiny_split())
    a = R.parse_args(["--dry-run", "--tau", "6000", "--split", "dev", "--out", str(tmp_path / "runs"),
                      "--tau-record", str(_frozen(tmp_path))])
    out = R.run_cli(a, scopes, grading, [], make_env=env_factory(pg_dsn, test_role_password), embedder=emb)
    (rdir,) = (tmp_path / "runs").iterdir()
    record = json.loads((rdir / "run.json").read_text())
    assert record["status"] == "complete" and record["tau_strong_q"] == 6000 and record["k"] == 3
    assert record["tau_record_sha256"] == hashlib.sha256((tmp_path / "TAU.json").read_bytes()).hexdigest()
    assert record["split_sha256"] == DEV_SHA256 and record["embedder_id"] == emb.embedder_id
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
    a = R.parse_args(["--recorded", str(tmp_path), "--tau", "6001", "--split", "dev", "--out", str(tmp_path / "o"),
                      "--tau-record", str(_frozen(tmp_path, tau=6001))])
    with pytest.raises(SystemExit, match="not a replay of it"):
        R.run_cli(a, scopes, grading, [])


# ---------------------------------------------------------------- tau freeze (select_exp0004_tau)

@pytest.mark.parametrize("extra, tau, message", [
    ([], "6000", "tau is not frozen"),                         # no record at the given path
    (["frozen"], "6001", "does not match the frozen"),
])
def test_a_run_refuses_to_start_without_a_matching_frozen_tau(tmp_path, extra, tau, message):
    record = _frozen(tmp_path) if extra else tmp_path / "missing.json"
    scopes, grading = split_views(tiny_split())
    a = R.parse_args(["--dry-run", "--tau", tau, "--out", str(tmp_path / "runs"), "--tau-record", str(record)])
    with pytest.raises(SystemExit, match=message):
        R.run_cli(a, scopes, grading, [])
    assert not (tmp_path / "runs").exists()                    # refused before any folder or database


def test_a_live_run_refuses_a_tau_selected_on_a_dry_dev_run(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "INSTRUMENT_APPROVED", True)
    monkeypatch.setenv("OPENAI_API_KEY", "set-but-never-read")
    a = R.parse_args(["--live", "--tau", "6000", "--out", str(tmp_path / "runs"), "--tau-record",
                      str(_frozen(tmp_path, mode="dry"))])
    with pytest.raises(SystemExit, match="not a dry one"):
        R.run_cli(a, [], {}, [])
    assert not (tmp_path / "runs").exists()


def test_the_dev_coverage_mode_needs_no_frozen_tau(tmp_path):
    a = R.parse_args(["--dry-run", "--dev-coverage", "--split", "dev", "--tau-record", str(tmp_path / "none.json")])
    assert a.tau is None and a.dev_coverage


def test_fresh_env_records_the_frozen_tau_as_a_config_event_in_the_org_stream(pg_dsn, test_role_password, tmp_path):
    from nacre.eval.select_exp0004_tau import check_frozen, config_event_content
    from nacre.ledger.read_stream import read_stream
    content = config_event_content(check_frozen(_frozen(tmp_path), tau=6000, live=False))
    with R.fresh_env(pg_dsn, role_password=test_role_password, tau_event=content) as env, \
            env.open_as(env.owner) as s:
        events = [e for e in read_stream(s, env.key_provider, env.org_id)
                  if isinstance(e.body, dict) and (e.body.get("content") or {}).get("op") == "recall_tau"]
    assert len(events) == 1 and events[0].body["content"] == content
    assert events[0].envelope.event_type.value == "config_event" and content["tau_strong_q"] == 6000


def test_end_to_end_dev_coverage_then_select_then_a_frozen_run(pg_dsn, test_role_password, tmp_path, emb,
                                                               monkeypatch):
    """Tiny synthetic split: dry dev-coverage run (sleep seats faked so beliefs exist) -> select -> recorded replay
    of the dev run reselects the same tau -> a non-dev dry run starts only with that frozen tau."""
    from nacre.eval.select_exp0004_tau import build_record
    monkeypatch.setattr(R, "DryProvider", EchoFake)
    scopes, grading = split_views(tiny_split())
    make_env = env_factory(pg_dsn, test_role_password)
    dev = R.parse_args(["--dry-run", "--dev-coverage", "--split", "dev", "--out", str(tmp_path / "dev")])
    out = R.run_cli(dev, scopes, grading, [], make_env=make_env, embedder=emb)
    assert set(out) == {"coverage_rows", "contradiction_links"} and out["coverage_rows"] == 3 * 8
    (ddir,) = (tmp_path / "dev").iterdir()
    links = out["contradiction_links"]                    # D-0030 condition 3: beside the tau rows and in run.json
    assert json.loads((ddir / "run.json").read_text())["contradiction_links"] == links
    assert len(json.loads((ddir / "link_rows.json").read_text())) == links["scopes"] == 3 * 2
    assert links["true_conflicts"] == 3 * 2 * 2 and links["pairs_judged"] > 0      # EchoFake's judge reply never parses
    assert (links["links"], links["correct_link_rate"], links["false_link_rate"]) == (0, 0.0, 0.0)
    rec = build_record(ddir, grading, date="2026-10-02")
    assert rec["dev_run"]["mode"] == "dry" and rec["selection"]["rows"] == 24 and rec["selection"]["strong_eligible"]
    replay = R.parse_args(["--recorded", str(ddir), "--dev-coverage", "--split", "dev", "--out", str(tmp_path / "rep")])
    R.run_cli(replay, scopes, grading, [], make_env=make_env, embedder=emb)
    (rdir,) = (tmp_path / "rep").iterdir()
    assert build_record(rdir, grading, date="2026-10-02")["selection"] == rec["selection"]
    (tmp_path / "TAU.json").write_text(json.dumps(rec))
    tau = str(rec["tau_strong_q"])
    wrong = R.parse_args(["--dry-run", "--tau", str(rec["tau_strong_q"] + 1), "--split", "dev", "--out",
                          str(tmp_path / "t"), "--tau-record", str(tmp_path / "TAU.json")])
    with pytest.raises(SystemExit, match="does not match the frozen"):
        R.run_cli(wrong, scopes, grading, [], make_env=make_env, embedder=emb)
    ok = R.parse_args(["--dry-run", "--tau", tau, "--split", "dev", "--out", str(tmp_path / "t"),
                       "--tau-record", str(tmp_path / "TAU.json")])
    out = R.run_cli(ok, scopes, grading, [], make_env=make_env, embedder=emb)
    assert out["summary"]["N"]["trials"] == 3 * 8


def test_main_refuses_before_reading_any_split(tmp_path, monkeypatch):
    def never(*a, **k):
        raise AssertionError("a split was read before the freeze check")
    monkeypatch.setattr(R, "load_split", never)
    with pytest.raises(SystemExit, match="tau is not frozen"):
        R.main(["--dry-run", "--tau", "6000", "--tau-record", str(tmp_path / "missing.json")])
