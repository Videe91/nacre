# Testing rules

## Mutation runs (D1, owner rule 2026-09-30)
- A mutation run must **never modify the working tree**. Use `scripts/run_mutants.py` (spec: a JSON list of
  `{file, old, new, tests}`). It:
  - copies HEAD plus the uncommitted changes into a temporary git worktree, and mutates only there;
  - runs the unmutated baseline first, and stops if it fails;
  - restores each mutant and checks the worktree is back to baseline;
  - removes the worktree, then asserts the real tree is byte-for-byte unchanged (git status plus a hash of every
    tracked and untracked file; exit 3 if not);
  - never writes bytecode in the worktree, and refuses to run if any `.pyc` is present. A cached `.pyc` with the same
    source size and mtime second turns a same-size mutant into a false SURVIVED (2026-10-01).
- No ad-hoc `sed`/`cp` mutation loops on the real tree. Why: on 2026-09-30 such a loop, piped through `head`,
  was killed before it restored `store_attachment.py`, leaving a security lock removed in the working tree. The
  tests caught it, but it must not be possible at all.
- Report every run: mutants run, killed, survived. Each survivor gets a test, or is documented as equivalent in
  the file header.

## Evidence
- Performance evidence uses the production connection setup (pooled, `core.db.open_pool`). Any other setup is
  labelled as such in the evidence file. Labels come from the code actually run, never from a docstring.

## Flaky tests (owner rule 2026-10-01)
- A flaky test is hunted, never retried or skipped:
  - restart the Docker DB cold;
  - run the full suite repeatedly in random order until it reproduces;
  - capture the name and output;
  - classify it as a test bug (fix the test) or a product race (fix the code, add a deterministic test for the
    interleaving, record it in CURRENT.md).
- Always run with `-rf` (or keep the junit XML), so a failing test's name is never lost.

## Running the suite before a commit (2026-10-01)
- Gate commits on **pytest's exit code AND `check_structure.py`'s exit code**, never on a piped `tail` (use `set -o pipefail`, or capture to a file and
  check `$?`). Commit 4b01a54 was pushed with 1 failing test because of this.
- Never `from conftest import ...` in a test file; `conftest` is not a unique module name across folders. Put shared
  helpers in a uniquely named module.
- Commit cde072d went in with `check_structure.py` failing (an unregistered file), because only pytest gated it. Fixed in the next commit; both now gate.
