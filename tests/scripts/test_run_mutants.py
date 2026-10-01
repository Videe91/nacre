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


def test_a_same_size_same_second_mutant_is_still_judged_on_its_own_code(monkeypatch):
    # The flaky failure of 2026-10-01, forced deterministically: the mutant has the SAME size and the SAME mtime as
    # the source the baseline compiled. Cached bytecode would run the original code and report SURVIVED.
    import os
    probe = ROOT / "tests" / "scripts" / "_probe_same_size_test.py"
    probe.write_text("def test_probe():\n    assert 1 + 1 == 2\n")
    real_write = rm._write_source

    def write_keeping_mtime(path, text):
        st = path.stat()
        real_write(path, text)
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
    monkeypatch.setattr(rm, "_write_source", write_keeping_mtime)
    try:
        (r,) = rm.run([{"file": "tests/scripts/_probe_same_size_test.py", "old": "1 + 1 == 2", "new": "1 + 1 == 3",
                        "tests": ["tests/scripts/_probe_same_size_test.py"]}], out=lambda _: None)
        assert r["outcome"] == "killed"
    finally:
        probe.unlink()


def test_bytecode_in_the_worktree_is_refused(tmp_path):
    (tmp_path / "pkg" / "__pycache__").mkdir(parents=True)
    (tmp_path / "pkg" / "__pycache__" / "m.cpython-314.pyc").write_bytes(b"x")
    import pytest
    with pytest.raises(RuntimeError, match="bytecode present"):
        rm._assert_no_bytecode(tmp_path)


def test_the_baseline_runs_each_mutants_exact_arguments(monkeypatch):
    # A flag's value ("-k", "x") must stay with its flag; an earlier baseline passed "x" to pytest as a path.
    seen = []
    monkeypatch.setattr(rm, "_pytest", lambda worktree, tests: seen.append(list(tests)) or 0)
    rm.run([{"file": "src/nacre/core/encode_cbor.py", "old": "    if arg < 24:", "new": "    if arg < 23:",
             "tests": ["tests/core/test_encode_cbor.py", "-k", "head"]}], out=lambda _: None)
    assert seen[0] == ["tests/core/test_encode_cbor.py", "-k", "head"]           # the baseline
