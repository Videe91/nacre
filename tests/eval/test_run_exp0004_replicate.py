"""Tests for eval/run_exp0004_replicate.py (and eval/provide_exp0004_models.py): one replicate of the tiny synthetic split
in a fresh database (capture -> gate -> sleep per day, erase_person, arms C / V / N, frame checks, replay of every
frame), its recorded replay with the network blocked, and the dev coverage mode."""
import re

import pytest

from exp0004_kit import EchoFake, HashEmbedder, env_factory, tiny_split
from nacre.eval.load_exp0004_set import split_views
from nacre.eval.provide_exp0004_models import LiveMode, RecordedMode, fixture_path
from nacre.eval.run_exp0004_replicate import TAU_PROBE, run_replicate
from nacre.recall.load_index_cache import IndexCache

GRADE_KEYS = ("task_id", "arm", "parsed", "ask", "success", "stale", "asked", "correct_ask", "injection", "cross_scope",
              "erased", "memory_items")


@pytest.fixture
def emb(monkeypatch):
    e = HashEmbedder()
    monkeypatch.setattr("nacre.recall.index_version.default_embedder", lambda: e)   # the index's embedder = recall's
    return e


def _run(pg_dsn, test_role_password, mode, emb, **kw):
    scopes, grading = split_views(tiny_split())
    with env_factory(pg_dsn, test_role_password)() as env:
        return run_replicate(env, scopes, grading, mode, rep=1, tau_strong_q=kw.pop("tau", 6000), embedder=emb,
                             cache=IndexCache(env.key_provider, dim=emb.dim), **kw), grading


def test_a_live_replicate_then_its_recorded_replay(pg_dsn, test_role_password, tmp_path, emb, no_network):
    fake = EchoFake()
    live, grading = _run(pg_dsn, test_role_password, LiveMode(fake, tmp_path / "fixtures"), emb)
    assert len(live.trials) == 2 * 4 * 3 and {t["arm"] for t in live.trials} == {"C", "V", "N"}
    f = live.frames
    assert f["frames"] == 8 and f["replay_replayed"] == 8 and f["items"] > 0
    for metric in ("ungranted_frame_item", "erased_content", "superseded_item", "untraced_frame", "replay_mismatch",
                   "non_authoritative_item"):
        assert f[metric] == 0, metric
    assert live.prompts_identical and live.sleep["promoted"] > 0 and live.sleep["erased_persons"] == 2
    assert live.fixtures == sum(1 for p in (tmp_path / "fixtures" / "rep1").glob("*.jsonl")
                                for _ in p.read_text().splitlines())
    rows = {(t["task_id"], t["arm"]): t for t in live.trials}
    assert not any(t["cross_scope"] for t in live.trials)              # the twin's facts reach no arm (no grant)
    for sid in ("x-s1-01", "x-s1-02"):
        assert not rows[(f"{sid}:task1", "N")]["erased"] and not rows[(f"{sid}:task1", "V")]["erased"]
        assert rows[(f"{sid}:task1", "C")]["success"] and rows[(f"{sid}:task4", "C")]["success"]   # "(none)" -> ask
        assert rows[(f"{sid}:task2", "N")]["success"]                  # the echoed frame holds the reviewer's fact
        assert rows[(f"{sid}:task1", "N")]["target_in_frame"] is False  # its only source was erased
        assert rows[(f"{sid}:task2", "N")]["target_in_frame"] is True
    blob = "".join(p.read_text() for p in (tmp_path / "fixtures").rglob("*.jsonl"))
    assert "x-s1-0" not in blob                                        # no dataset id ever reached a prompt
    for g in grading.values():
        assert not any(rx and rx in blob for rx in g.regexes.values())
    assert any("eval.exp0004.transfer.N" in line for line in blob.splitlines())

    replay, _ = _run(pg_dsn, test_role_password, RecordedMode(tmp_path / "fixtures"), emb)
    assert replay.sleep["calls_live"] == 0 and replay.sleep["calls_reused"] == live.sleep["calls_live"]
    assert {k: {g: t[g] for g in GRADE_KEYS} for k, t in rows.items()} == \
        {(t["task_id"], t["arm"]): {g: t[g] for g in GRADE_KEYS} for t in replay.trials}
    assert replay.frames["replay_mismatch"] == 0 and replay.prompts_identical


def test_recorded_mode_without_a_fixture_fails_loudly(pg_dsn, test_role_password, tmp_path, emb):
    with pytest.raises(FileNotFoundError):
        _run(pg_dsn, test_role_password, RecordedMode(tmp_path / "nothing"), emb)


def test_dev_coverage_mode_writes_rows_and_calls_no_transfer(pg_dsn, test_role_password, emb):
    fake = EchoFake()
    res, grading = _run(pg_dsn, test_role_password, LiveMode(fake), emb, tau=TAU_PROBE, transfers=False)
    assert res.trials == [] and not any(c.startswith("eval.") for c in fake.calls)
    assert len(res.coverage_rows) == 8
    for row in res.coverage_rows:
        assert set(row) == {"rep", "task_id", "category", "answerable", "coverage", "top_semantic", "items"}
        assert row["answerable"] == (not grading[row["task_id"]].expected_ask)
        assert row["coverage"] in ("strong", "weak", "none")
        assert row["top_semantic"] is None or isinstance(row["top_semantic"], int)
    assert res.frames["replay_mismatch"] == 0 and res.frames["untraced_frame"] == 0


def test_fixture_paths_are_per_replicate_scope_and_part(tmp_path):
    assert fixture_path(tmp_path, 2, "x-s1-01", "history") == tmp_path / "rep2" / "x-s1-01.history.jsonl"
    assert re.fullmatch(r"rep\d+/[a-z0-9-]+\.tasks\.jsonl",
                        fixture_path(tmp_path, 3, "e4d-s2-05", "tasks").relative_to(tmp_path).as_posix())
    with pytest.raises(ValueError):
        fixture_path(tmp_path, 1, "x", "all")
