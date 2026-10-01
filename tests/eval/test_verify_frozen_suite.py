"""Tests for eval/verify_frozen_suite.py (Phase 2 gate item 1)."""
import shutil
from pathlib import Path

import pytest

from nacre.eval.verify_frozen_suite import verify_frozen_suite

ROOT = Path(__file__).resolve().parents[2] / "tests" / "regression"
MNEXA = Path.home() / "Desktop" / "mnexa"
RUNS = Path.home() / "Desktop" / "nacre-runs"


def test_the_committed_suite_matches_its_manifests():
    r = verify_frozen_suite(ROOT)
    assert r.ok, r.problems[:5]
    assert r.files_checked >= 28 + 2 + 29 + 2 + 5 + 21          # tasks, graders, results, regrades, 030-035, EXP-0001


@pytest.mark.skipif(not (MNEXA.is_dir() and RUNS.is_dir()), reason="external originals not on this machine")
def test_external_hash_only_entries_match_when_present():
    r = verify_frozen_suite(ROOT, mnexa_root=MNEXA, runs_root=RUNS)
    assert r.ok, r.problems[:5]
    assert r.external_checked > 1000 and len(r.external_roots_checked) == 2


def _copy(tmp_path):
    dst = tmp_path / "regression"
    shutil.copytree(ROOT, dst)
    return dst


def test_one_changed_byte_is_reported(tmp_path):
    root = _copy(tmp_path)
    f = root / "mnexa" / "tasks" / "tasks_014.json"
    data = bytearray(f.read_bytes()); data[100] ^= 1
    f.write_bytes(bytes(data))
    r = verify_frozen_suite(root)
    assert not r.ok and r.problems == [f"sha256 mismatch: {f}"]


def test_a_missing_file_is_reported(tmp_path):
    root = _copy(tmp_path)
    (root / "exp0001" / "summary.json").unlink()
    r = verify_frozen_suite(root)
    assert any("missing:" in p and p.endswith("summary.json") for p in r.problems)


def test_absent_external_roots_are_not_reported_as_checked(tmp_path):
    r = verify_frozen_suite(ROOT, mnexa_root=tmp_path / "nope", runs_root=tmp_path / "nope")
    assert r.ok and r.external_checked == 0 and r.external_roots_checked == []
