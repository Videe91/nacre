"""Tests for scripts/run_mutants.py: mutants run in a temporary worktree; the real working tree never changes."""
import importlib.util
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("run_mutants", ROOT / "scripts/run_mutants.py")
rm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rm)
TESTS = ["tests/core/test_encode_cbor.py"]


def _worktrees():
    return subprocess.run(["git", "worktree", "list"], cwd=ROOT, capture_output=True, text=True).stdout


def test_mutants_are_judged_in_a_worktree_and_the_real_tree_is_unchanged():
    before, status, trees = rm.snapshot(), _worktrees(), []
    results = rm.run([
        {"file": "src/nacre/core/encode_cbor.py", "old": "    if arg < 24:", "new": "    if arg < 23:", "tests": TESTS},
        {"file": "src/nacre/core/encode_cbor.py", "old": "    out = bytearray()\n",
         "new": "    out = bytearray()  # equivalent\n", "tests": TESTS},
        {"file": "src/nacre/core/encode_cbor.py", "old": "no such text", "new": "x", "tests": TESTS},
    ], out=trees.append)
    assert [r["outcome"] for r in results] == ["killed", "SURVIVED", "not-found"]
    assert rm.snapshot() == before and _worktrees() == status                # tree unchanged, worktree removed
    assert trees[-1] == "working tree unchanged (verified)"
    assert "if arg < 24:" in (ROOT / "src/nacre/core/encode_cbor.py").read_text()


def test_uncommitted_changes_are_what_gets_mutated(tmp_path):
    # The worktree carries the working tree's uncommitted state, so a mutant of a new, untracked file is testable.
    probe = ROOT / "tests" / "scripts" / "_probe_untracked_test.py"
    probe.write_text("def test_probe():\n    assert 1 + 1 == 2\n")
    try:
        (r,) = rm.run([{"file": "tests/scripts/_probe_untracked_test.py", "old": "1 + 1 == 2", "new": "1 + 1 == 3",
                        "tests": ["tests/scripts/_probe_untracked_test.py"]}], out=lambda _: None)
        assert r["outcome"] == "killed"
    finally:
        probe.unlink()
