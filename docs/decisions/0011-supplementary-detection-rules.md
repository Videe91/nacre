# D-0011: Nacre supplementary detection rules

- **Status:** accepted (owner, 2026-09-30, with conditions below)
- **Tier:** D2 (changes what strip_secrets detects; cross-module behaviour at intake)
- **Date:** 2026-09-30
- **Relies on assumptions:** A-0010, A-0018
- **Related:** D-0007 (gitleaks rules + entropy check), D-0009 (google-re2), D-0010 (the pre-commit hook will use strip_secrets)
- **Evidence:** `docs/assumptions/evidence/A-0010-measurement-2026-09-30.md`

## Amendment history (owner conditions, before acceptance)
1. **Sealed holdout.** The corpus is split into:
   - a **working set**, visible while writing rules;
   - a **holdout**, never used for rule writing.

   Official catch rates come from the holdout only. Both are reported, and any gap between them is
   flagged as overfitting. The holdout is committed **before** any Nacre rule is written, so git
   history shows it was fixed first.
2. **Additive only.** Nacre rules add detections. They never disable, override or shadow a vendored
   gitleaks rule without an ADR. The loader refuses duplicate rule ids, and Nacre allowlists apply
   only to Nacre rules.
3. **Upstream.** Once proven, the `ghs_` stateless and password-in-URL rules are prepared as
   contributions to gitleaks. Not blocking.
4. **Rule approval.**
   - **Tightening changes** (adding a rule, or narrowing a rule so it catches more real secrets or fewer
     non-secrets without allowlisting) are local changes in the rules file, under the admission
     criteria below.
   - **Loosening changes** (any new or wider allowlist, removing a rule, relaxing a pattern) need the
     owner's explicit approval.
   - The placeholder allowlist of `url-userinfo-password` below is approved as part of this ADR.

5. **2026-09-30 — re-seal after an observed holdout failure (owner, post-acceptance).**
   - A holdout whose failures have been seen by a rule writer is **demoted to working data**.
   - A **fresh holdout** is sealed (new seed, new embedding contexts), chosen **before any fix is
     written**, by a **separate session that does not see rule code**.
   - A **holdout-generation log** (`docs/assumptions/evidence/A-0010-holdout-log.md`) records which holdout
     produced each official number.
   - The fresh holdout tests **every covered rule, vendored and Nacre, in every embedding context**,
     documents included.
6. **2026-09-30 — boundary principle (owner).** A token ends at the first character outside its format's
   specified character set, not at a list of known terminators. Boundaries are derived from the
   specs (RFC 7519/7515 for JWTs, Sentry's source for `sntryu_`, and so on). No special cases for
   particular surrounding syntax (XML, error messages, …).
   Vendored rules are not edited (additive only): a spec-bounded Nacre rule is added for each covered
   format.
7. **2026-09-30 — loosening declined (owner).** The `[...]` / `{...}` placeholder extension of
   `url-userinfo-password` is declined: real passwords can contain brackets or braces. Repo false
   positives stay on exact-value hook allowlists. If a need appears, the only acceptable proposal is
   narrow: placeholders that contain a credential word, measured on the holdout.

8. **2026-09-30 — benchmark method (owner, D2).**
   - **Negatives:** synthetic negatives are never placed in credential slots.
   - **A third category, "random value in a credential slot":** random non-secret values (hashes,
     blobs, ids) placed in contexts that name a credential. The expected behaviour is **strip**, and
     stripping is scored as correct. It is a separate group, not a false positive.
   - **Caught:** every character of the secret part is redacted. Documented public prefixes and format
     markers may remain.
   - **Longest surviving secret fragment:** reported as a metric.
   - **Gating:** these definitions gate from **H3** onward. H2 is recomputed under them for the record.
   - **H2's FP result:** the 3.25% stays in the holdout log, marked *invalid by construction*
     (synthetic negatives in credential slots). H2 is not altered.
   - **H3:** sealed like H2 (separate agent, new seed, sealed before any run). The official FP rate
     comes from H3.
   - **Test runs:** the holdout measurement is a separate slow test marker (`holdout`). It runs when rules,
     detector code or the corpus change, not on every commit.

## Admission criteria for a Nacre rule (written once; every rule entry carries these fields)
- `source`: provider-owned URL(s) or a standard, never a scanner rule set; `source_date`: when it was checked.
- `fact_ids`: A-IDs for any owner-measured fact the rule depends on (empty if none).
- `test`: the test that exercises it.
- `holdout`: the holdout catch rate of its group when admitted (≥ 99%), plus the FP rate with it
  enabled (≤ 2%).
- A rule is admitted only if the full A-0010 measurement still passes with it enabled.

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

## Decision
Option 1. `src/nacre/ledger/data/nacre-rules-v1.toml`, first contents:

| id | Pattern (RE2) | Secret | Source |
|---|---|---|---|
| `url-userinfo-password` | `[a-zA-Z][a-zA-Z0-9+.-]*://[^\s:/?#@'"]+:([^\s/?#@'"]+)@` | group 1 | RFC 3986 §3.2.1; the libpq, MySQL and MongoDB URI docs. Allowlist: placeholders such as `${VAR}`, `$VAR`, `<password>`, `***`, `password`, `xxx` |
| `github-installation-token-stateless` | `ghs_[A-Za-z0-9.\-_]{36,}` | match | GitHub changelog 2026-05-15 (GitHub's own recommended regex) |
| `heroku-oauth-token` | `HRKU-(?:[0-9a-fA-F-]{36}\|[A-Za-z0-9_-]{60})` | match | Heroku devcenter (65 chars; UUID example). The 60-char charset is pending owner measurement; until then only the UUID form is asserted |
| `supabase-secret-key` | `sb_secret_[A-Za-z0-9_-]{22}_[A-Za-z0-9_-]{8}` | match | Supabase's own key generator |
| `sentry-org-token-full` | `sntrys_[A-Za-z0-9+/=]+_[A-Za-z0-9+/]{43}` | match | getsentry/sentry `orgauthtoken_token.py` |

- **Growth:** later rules from owner-measured facts are tightening changes under the admission
  criteria (amendment 4). Loosening needs the owner's explicit approval.
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
