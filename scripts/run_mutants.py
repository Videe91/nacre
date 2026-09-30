"""
Run hand-written mutants against the test suite WITHOUT ever touching the working tree (owner rule, 2026-09-30;
.claude/rules/testing.md).

How:
  1. Snapshot the working tree: `git status --porcelain` plus a hash of every tracked and untracked file.
  2. Create a temporary git worktree at HEAD, then copy in the uncommitted changes (the diff, plus untracked files)
     so mutants run against exactly the code under review.
  3. Run the baseline (unmutated) tests there. If the baseline fails, stop: every mutant would look "killed".
  4. For each mutant: apply it in the worktree, run its tests, restore the file, and check the worktree is back to
     its baseline state.
  5. Remove the worktree, then assert the real working tree is byte-for-byte unchanged (exit 3 if not).
Mutations only ever happen inside the temporary worktree, so an interrupted run cannot damage the real tree.
`git worktree prune` at start removes worktrees left registered by an interrupted run.

Spec (JSON): [{"file": "src/...py", "old": "exact text", "new": "replacement", "tests": ["tests/...py", "-k", "x"]}]
Run:   .venv/bin/python scripts/run_mutants.py spec.json
Exit:  0 = all mutants ran (survivors are reported, not failures); 2 = baseline failed; 3 = real tree changed.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYTEST = [str(ROOT / ".venv" / "bin" / "python"), "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"]


def _git(*args: str, cwd: Path = ROOT, stdin: bytes | None = None) -> bytes:
    return subprocess.run(["git", *args], cwd=cwd, input=stdin, capture_output=True, check=True).stdout


def snapshot(root: Path = ROOT) -> str:
    """Status plus the content of every tracked and untracked (non-ignored) file."""
    h = hashlib.sha256(_git("status", "--porcelain=v1", "-uall", cwd=root))
    for name in sorted(_git("ls-files", "-c", "-o", "--exclude-standard", "-z", cwd=root).decode().split("\0")):
        path = root / name
        if name and path.is_file():
            h.update(name.encode() + b"\0" + path.read_bytes())
    return h.hexdigest()


def _prepare(worktree: Path) -> None:
    diff = _git("diff", "HEAD", "--binary")
    if diff:
        _git("apply", "--whitespace=nowarn", cwd=worktree, stdin=diff)
    for name in _git("ls-files", "-o", "--exclude-standard", "-z").decode().split("\0"):
        if name:
            (worktree / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, worktree / name)


def _pytest(worktree: Path, tests: list[str]) -> int:
    env = dict(os.environ, COMPOSE_PROJECT_NAME=os.environ.get("COMPOSE_PROJECT_NAME", ROOT.name))  # same DB container
    return subprocess.run([*PYTEST, *tests], cwd=worktree, env=env, capture_output=True).returncode


def run(mutants: list[dict], out=print) -> list[dict]:
    before = snapshot()
    _git("worktree", "prune")
    worktree = Path(tempfile.mkdtemp(prefix="nacre-mutants-")) / "wt"
    _git("worktree", "add", "--detach", "--quiet", str(worktree), "HEAD")
    results = []
    try:
        _prepare(worktree)
        baseline = snapshot(worktree)
        tests = sorted({t for m in mutants for t in m["tests"] if not t.startswith("-")})
        if _pytest(worktree, tests) != 0:
            out("baseline tests FAIL without any mutant; fix them first")
            raise SystemExit(2)
        for m in mutants:
            target = worktree / m["file"]
            original = target.read_text()
            if m["old"] not in original:
                results.append({**m, "outcome": "not-found"})
                out(f"NOT FOUND  {m['file']}: {m['old']!r}")
                continue
            target.write_text(original.replace(m["old"], m["new"], 1))
            try:
                code = _pytest(worktree, m["tests"])
            finally:
                target.write_text(original)
            if snapshot(worktree) != baseline:
                raise RuntimeError(f"worktree not restored after mutating {m['file']}")
            outcome = {0: "SURVIVED", 1: "killed"}.get(code, f"error (pytest exit {code})")
            results.append({**m, "outcome": outcome})
            out(f"{outcome:<10} {m['file']}: {m['old']!r}")
    finally:
        _git("worktree", "remove", "--force", str(worktree))
        shutil.rmtree(worktree.parent, ignore_errors=True)
        if snapshot() != before:
            out("FATAL: the real working tree changed during the mutation run")
            raise SystemExit(3)
    out("working tree unchanged (verified)")
    return results


if __name__ == "__main__":
    run(json.loads(Path(sys.argv[1]).read_text()))
