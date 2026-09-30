# D-0010: Pre-commit secret scan

- **Status:** accepted (owner decided, 2026-09-30)
- **Tier:** D2 (repo workflow; `google-re2` in every dev environment)
- **Date:** 2026-09-30
- **Relies on assumptions:** A-0018
- **Related:** D-0007 (rules), D-0009 (engine)

## Context
The A-0017 evidence push showed that secret-shaped strings reach commits easily. GitHub push
protection caught them only at push time, after they were already in local history. Nacre stores
agent memory, so the repo must never be where a secret leaks.

## Options considered
1. **Pre-commit hook scanning staged content with the vendored gitleaks rules via `google-re2`.**
   Catches secrets before they enter history, using the same rules the product uses.
2. **Rely on GitHub push protection only.** Too late: the secret is already committed locally,
   and GitHub's patterns are not ours.
3. **The `pre-commit` framework running the gitleaks binary.** Adds a Go binary and another tool
   to every contributor's setup.

## Decision
Option 1.
- **Hook:** `scripts/hooks/pre-commit` runs `scripts/scan_staged_secrets.py` over the *staged*
  content of added or modified files.
- **Install:** `git config core.hooksPath scripts/hooks` (documented in CURRENT.md).
- **Phase 1:** the scanner is a temporary script. It supports the gitleaks features needed to
  keep noise down: keywords prefilter, per-rule entropy, global and per-rule allowlists
  (regexes, stopwords).
- **Later:** once INDEX #6 is built, the hook calls `ledger/strip_secrets.py` instead and the
  temporary script is deleted.
- **Bypass:** `git commit --no-verify` remains possible. Any bypass must be stated in the commit
  message.

## Why this one
It blocks at the earliest point, with no extra toolchain, and it dogfoods the rules Nacre ships.

## Consequences
- Every dev environment needs `google-re2` (already a runtime dependency per D-0009).
- A false positive blocks a commit. Fix it with an allowlist entry in the scanner (Phase 1) or in
  `strip_secrets` (later), never by weakening a rule.

## How we'd know it was wrong
- Frequent false-positive blocks lead people to use `--no-verify` routinely.
- A secret reaches GitHub despite the hook.
