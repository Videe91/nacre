# D-0011: Nacre supplementary detection rules

- **Status:** proposed — awaiting owner approval
- **Tier:** D2 (changes what strip_secrets detects; cross-module behaviour at intake)
- **Date:** 2026-09-30
- **Relies on assumptions:** A-0010, A-0018
- **Related:** D-0007 (gitleaks rules + entropy check), D-0009 (google-re2), D-0010 (the pre-commit hook will use strip_secrets)
- **Evidence:** `docs/assumptions/evidence/A-0010-measurement-2026-09-30.md`

## Context
The first A-0010 measurement of `strip_secrets` (gitleaks v8.30.1 under RE2, the public-credential layer
and the entropy layer) meets the false-positive target at 0.36% of negative documents. Every public value
is kept. But it misses the ≥ 99% per-group target in 8 groups, for two reasons that tuning cannot fix:

| Group | Catch | Cause |
|---|---|---|
| Passwords in URLs / DB connection strings (4 kinds) | 0–4% | gitleaks v8.30.1 has **no rule** for a password in URL userinfo |
| Sentry (legacy DSN secret) | 0% of that kind | same (the secret sits in URL userinfo) |
| GitHub stateless `ghs_` (2026 format) | entropy-only in cued contexts | the format post-dates gitleaks v8.30.1 |
| Heroku `HRKU-`, Supabase `sb_secret_`, Sentry `sntrys_` | caught by rules only inside a `Bearer` header | the gitleaks rules need a keyword context (e.g. a variable name) or a charset that the documented format breaks (`sntrys_` payloads contain `+ / =`) |

The corpus deliberately uses neutral variable names and log lines. An agent's event does not come with
a helpful variable name.

## Options considered
1. **A small Nacre rules file beside the vendored one.**
   - Same TOML shape, loaded by the same code under RE2, pinned by its own sha256.
   - Each rule is derived **only from provider-owned sources or a standard**, and cites them.
   - Rules are never derived from scanner rule sets (the D-0007 independence rule), and every rule is
     measured on the corpus and the negatives.
2. **Upgrade gitleaks.** It might add the stateless `ghs_` rule, but nothing indicates it would add URL
   userinfo or context-free prefix matching. It also changes 200+ rules at once, which needs the
   Go-comparison re-run (D-0009).
3. **Relax the targets for these groups.** Contradicts the owner's per-provider ≥ 99% rule.
4. **Widen the entropy layer to catch them.** Tried: it catches long tokens only by accident, and doing it
   on purpose raised the FP rate to 4.1% (base64 blobs). Rejected.

## Decision (proposed)
Option 1. `src/nacre/ledger/data/nacre-rules-v1.toml`, first contents:

| id | Pattern (RE2) | Secret | Source |
|---|---|---|---|
| `url-userinfo-password` | `[a-zA-Z][a-zA-Z0-9+.-]*://[^\s:/?#@'"]+:([^\s/?#@'"]+)@` | group 1 | RFC 3986 §3.2.1; the libpq, MySQL and MongoDB URI docs. Allowlist: placeholders such as `${VAR}`, `$VAR`, `<password>`, `***`, `password`, `xxx` |
| `github-installation-token-stateless` | `ghs_[A-Za-z0-9.\-_]{36,}` | match | GitHub changelog 2026-05-15 (GitHub's own recommended regex) |
| `heroku-oauth-token` | `HRKU-(?:[0-9a-fA-F-]{36}\|[A-Za-z0-9_-]{60})` | match | Heroku devcenter (65 chars; UUID example). The 60-char charset is pending owner measurement; until then only the UUID form is asserted |
| `supabase-secret-key` | `sb_secret_[A-Za-z0-9_-]{22}_[A-Za-z0-9_-]{8}` | match | Supabase's own key generator |
| `sentry-org-token-full` | `sntrys_[A-Za-z0-9+/=]+_[A-Za-z0-9+/]{43}` | match | getsentry/sentry `orgauthtoken_token.py` |

- **Growth:** later rules from owner-measured facts (D-0007 amendment 4) are added to this file.
  **Proposed:** each such addition is a D1 change, recorded in the file with its source and measured
  on the corpus, not a new ADR each time. The owner may prefer an ADR amendment per batch instead.
- **Boundaries:** the rules must match in every corpus context, including plain log lines. Every rule
  change re-runs the A-0010 measurement.

## Why this one
It closes every current gap from provider-owned sources, keeps the vendored gitleaks file unmodified,
and keeps each rule measured and attributable.

## Consequences
- Two rule files to pin and load. The hook switch (D-0010) waits until the targets are met with them.
- `url-userinfo-password` is the rule most likely to produce false positives (example URLs in docs). Its
  placeholder allowlist is measured on the negatives.
- A-0018's Go-agreement guard covers only the vendored gitleaks rules. Nacre rules are guarded by their
  own tests.

## How we'd know it was wrong
- The false-positive rate rises above 2% after adding a rule.
- A rule's catch rate depends on a context the corpus doesn't model (e.g. multi-line values).
- The rules file becomes a second, drifting copy of gitleaks.
