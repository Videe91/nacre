# A-0017 evidence: gitleaks rules under Python `re` vs `google-re2` (vs Go, gitleaks' own engine)

- **Date:** 2026-09-30
- **Assumption under test:** A-0017, "gitleaks' rules (written for Go RE2) compile and match identically
  under Python `re`". The owner added two tests: identical semantics to gitleaks' rules, and no
  catastrophic backtracking on untrusted input.
- **Verdict:** **A-0017 is invalidated.** Python `re` fails on compilation, on semantics and on
  backtracking. `google-re2` passes all three measured tests.
- **Rule set:** gitleaks v8.30.1 `config/gitleaks.toml`, commit `83d9cd684c87d95d656c1458ef04895a7f1cbd8e`,
  sha256 `e163e53b9e7e8a8511e77271e2b323ed057759542a6d988258afe3a1fa329caf`. It has 222 rules; 221 have a
  regex (`pkcs12-file` matches file paths only).
- **Engines:**
  - Python 3.14.3 `re`, run in two modes: default (Unicode) and `re.ASCII`;
  - `google-re2` 1.1.20251105;
  - Go `regexp` (Go toolchain on this machine), gitleaks' own engine and the ground truth.
- **Scripts and raw data:** `scripts/a0017/`, `docs/assumptions/evidence/A-0017/*.json`. Example match
  strings in the JSON are redacted: the synthetic, secret-shaped strings were blocked by GitHub push
  protection on first push. Counts are unchanged.

## Method
1. **Compilation** of every rule under each engine. Warnings are recorded.
2. **Semantics:** the lists of matched strings are compared per rule and per document, across:
   - a real-text corpus: 1,200 files (14.4 M characters) of Python packages, docs and configs from
     this repo's `.venv`;
   - up to 30 hypothesis-generated positives per rule (5,970 total), each embedded in a config line.

   Go's matches are the reference. For the 22 rules Python cannot compile, positives came from a
   generation-only rewrite (1,320 more documents); Go still judged every match.
3. **Backtracking:** 16 or more adversarial probes of 20,000 characters per rule:
   - uniform runs of 16 characters, e.g. `A…`, `0…`, `_…`, `\n…`;
   - keyword-led runs;
   - repeated near-miss matches;
   - repeated half-prefixes of real matches.

   Each rule and engine runs in a subprocess with a **per-probe** 10-second timeout. For the slowest
   rules, the growth of search time with input length was measured separately.

## Results

### 1. Compilation

| Engine | Rules compiled (of 221) | Failures / misreads |
|---|---|---|
| Go `regexp` | 221 | — |
| `google-re2` | **221** | none |
| Python `re` | **199** | **22 fail**: `(?i)` in mid-pattern ("global flags not at the start"), legal in Go, an error in Python ≥3.11. Examples: `sendgrid-api-token`, `gocardless-api-token`, `alibaba-access-key-id`, `planetscale-password` |
| | | **1 silent misread**: `airtable-personnal-access-token` uses the POSIX class `[[:alnum:]]`; Python compiles it as a different set, with only a FutureWarning |

### 2. Semantics against Go (the ground truth)

| Engine | Rules disagreeing with Go | Cause |
|---|---|---|
| `google-re2` | **0 of 221** (corpus + all 7,290 generated documents) | — |
| Python `re.ASCII` | **10 of 199** compiled rules | 9 rules: `\s` matches `\v` (0x0B) in Python but not in Go or RE2 (e.g. `anthropic-api-key`, `jwt`, `sonar-api-token`); 1 rule: the POSIX-class misread above (30 of 30 positive docs wrong) |
| Python `re` (Unicode) | **157 of 199** vs re2 | `\s`, `\w`, `\d`, `\b` are Unicode-aware in Python and ASCII-only in Go |

### 3. Catastrophic backtracking (per-probe worst case, 20,000-character input)

| Engine | Median rule | Worst rules |
|---|---|---|
| `google-re2` | **0.00016 s** | **0.018 s** (`pypi-upload-token`); no rule above 0.1 s |
| Python `re.ASCII` | 0.012 s | **`curl-auth-user`: exponential.** 0.91 s at 250 chars, over 20 s at 500 chars, over 10 s at 20,000 chars (probe: repeated half-prefix) |
| | | Linear but slow (~2× time per doubling): `cohere-api-token` 1.21 s, `okta-access-token` 1.16 s, `cisco-meraki-api-key` 1.15 s, `sumologic-access-id` 1.15 s, `privateai-api-token` 0.64 s |
| Python `re` (Unicode) | 0.013 s | the same, plus `sumologic-access-id` at 3.37 s |

**What this means at intake:** payloads are untrusted, and a single 500-character crafted string
would freeze a Python-`re` intake worker indefinitely on `curl-auth-user`. The linear-but-slow rules
cost about 1 s per 20 KB each, so a 1 MB payload would take roughly a minute. RE2 guarantees
linear time by construction; the measured worst case is 18 ms per 20 KB.

## Integrity notes (reported, not hidden)
- **Flawed first run:** the first timing run applied the 10 s timeout to *all probes of a rule
  together*. It reported 5 "timeouts", 4 of which were only linear-but-slow rules whose probes added
  up. The script was fixed (per-probe timeout) and rerun; the numbers above come from the corrected
  run. The flawed output is kept as `results_run1_flawed_cumulative_timeout.json`.
- **Thin positives for 22 rules:** the rules Python cannot compile got positives from a
  generation-only rewrite. Only 74 of their 1,320 documents actually match in Go, and many rules
  have a single match, so agreement there rests mostly on negatives. The labeled corpus in #6
  (D-0007) must give these rules real positives.
- **Synthetic positives:** they come from Python's reading of each pattern (hypothesis `from_regex`).
  They are probes, not a labelled secret corpus. A-0010's catch-rate numbers still need that corpus.
- **Gitleaks features beyond the regex are not evaluated here:** `keywords` prefilter (221 rules),
  per-rule `entropy` (130), per-rule `allowlists` (9, including `regexTarget`, `stopwords`,
  `condition`), `secretGroup` (1), the global allowlist (25 path patterns, 13 regexes, 2 stopword
  lists), and `path` rules (5). `strip_secrets` must implement or explicitly exclude each (D-0007).

## `google-re2` package facts
- Official Google Python binding of RE2 (`github.com/google/re2`), license **BSD-3-Clause**. The
  PyPI metadata leaves the license field empty; the repository license applies.
- Version 1.1.20251105 ships CPython 3.14 wheels for macOS (arm64, x86_64), manylinux (x86_64,
  aarch64) and Windows, plus an sdist. It has no Python dependencies.

## Options for the owner
1. **Adopt `google-re2` as a runtime dependency (recommended).** It is the only engine here that
   meets both owner tests: identical to Go on 221 of 221 rules, and linear time. It needs a D2 ADR
   (proposed as D-0009).
2. **Python `re` + a translation layer + input caps + per-rule timeouts.** You would have to rewrite
   22 rules (mid-pattern flags), 1 POSIX class and `\s`→`[\t\n\f\r ]` everywhere, and run each rule
   under a hard timeout. It still leaves an exponential rule, `curl-auth-user`, that must be
   rewritten or dropped, and a timeout is a detection gap, not a fix. Not recommended.
3. **Run gitleaks itself (Go binary) as a subprocess.** It has exact semantics, but adds a process
   boundary and a non-Python toolchain to intake. Heavier than option 1 for no measured gain.
