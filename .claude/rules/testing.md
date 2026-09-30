# Testing rules

## Mutation runs (D1, owner rule 2026-09-30)
- A mutation run must **never modify the working tree**. Use `scripts/run_mutants.py` (spec: a JSON list of
  `{file, old, new, tests}`). It:
  - copies HEAD plus the uncommitted changes into a temporary git worktree, and mutates only there;
  - runs the unmutated baseline first, and stops if it fails;
  - restores each mutant and checks the worktree is back to baseline;
  - removes the worktree, then asserts the real tree is byte-for-byte unchanged (git status plus a hash of every
    tracked and untracked file; exit 3 if not).
- No ad-hoc `sed`/`cp` mutation loops on the real tree. Why: on 2026-09-30 such a loop, piped through `head`,
  was killed before it restored `store_attachment.py`, leaving a security lock removed in the working tree. The
  tests caught it, but it must not be possible at all.
- Report every run: mutants run, killed, survived. Each survivor gets a test, or is documented as equivalent in
  the file header.

## Evidence
- Performance evidence uses the production connection setup (pooled, `core.db.open_pool`). Any other setup is
  labelled as such in the evidence file. Labels come from the code actually run, never from a docstring.
