# D-0009: Regex engine for secret detection — `google-re2`

- **Status:** accepted (owner, 2026-09-30, with amendment below)
- **Tier:** D2 (runtime dependency)
- **Date:** 2026-09-30
- **Relies on assumptions:** A-0018 (A-0017 invalidated)
- **Related:** D-0007 (vendored gitleaks rules; it named "add google-re2 (new dependency ADR)" as the fallback if A-0017 failed), D-0006 (dependencies)
- **Evidence:** `docs/assumptions/evidence/A-0017-regex-engines.md`

## Amendment history (owner-directed, before acceptance)
1. **Re-verify on upgrade:** re-run the Go ground-truth comparison (`scripts/a0017/`) whenever the
   pinned gitleaks version changes. It does not run on every test run. This keeps A-0018 verified
   across upgrades. A gitleaks upgrade is not complete until that comparison has been re-run and
   its evidence committed.

## Context
D-0007 vendors gitleaks' rules, which are written for Go's RE2 engine. A-0017 bet that Python `re`
would run them identically. The owner asked for a comparison with `google-re2` on two criteria:
identical semantics to gitleaks, and no catastrophic backtracking on untrusted input. The evaluation
invalidated A-0017:
- Python `re` cannot compile 22 of 221 rules, silently misreads 1, and disagrees with Go on 10 more.
- One rule (`curl-auth-user`) backtracks exponentially: over 20 s on a 500-character input.
- `google-re2` matched Go on 221 of 221 rules, with a worst case of 18 ms per 20 KB.

## Options considered
1. **`google-re2` runtime dependency.** Identical to Go on every rule, and linear time by
   construction. It is Google's official binding, BSD-3-Clause, with wheels for CPython 3.14 on
   macOS, Linux and Windows. Cost: a compiled dependency.
2. **Python `re` + translation layer + input caps + per-rule timeouts.** No new dependency. But it
   means owning rewrites of 23 rules and `\s` everywhere, still leaves an exponential rule to drop or
   rewrite, and turns timeouts into silent detection gaps. Fails the owner's backtracking criterion.
3. **gitleaks binary as a subprocess.** Exact semantics. But it adds a Go toolchain and a process
   boundary on the intake path, with no measured gain over option 1.

## Decision
Option 1:
- **Pin:** `google-re2 == 1.1.20251105` as a runtime dependency. The pin is exact because match
  semantics are part of intake behaviour; upgrades mean a new pin plus a re-run of the A-0017
  comparison and the A-0010 corpus.
- **Only engine:** `strip_secrets` compiles every vendored rule with `re2` and never falls back to
  Python `re`. A rule that fails to compile under `re2` fails the load; it is never skipped silently.
- **Guard test:** the #6 test suite includes a copy of the Go-agreement check on the labeled corpus,
  so semantics are guarded, not assumed.

## Why this one
It is the only option that meets both owner criteria on measured evidence, and it is the fallback
D-0007 already named.

## Consequences
- A compiled dependency in `pyproject.toml`; the deployment platforms listed above all have wheels.
- The 22 rules Python couldn't compile have thin positive coverage so far. The #6 labeled corpus
  must give each rule real positives.
- The gitleaks features beyond regex (keywords, per-rule entropy, allowlists, `secretGroup`,
  stopwords, path rules) are still `strip_secrets`' job (D-0007).

## How we'd know it was wrong
- The #6 Go-agreement guard finds a disagreement on the labeled corpus (A-0018).
- A `google-re2` release stops shipping wheels for a supported platform or Python version.
- RE2's memory budget rejects a future gitleaks rule at compile time.
