# A-0010 holdout-generation log (D-0011 amendments 1, 5)

Which holdout produced each official number. A holdout is **official** until a rule writer has seen its
failures and written a fix; then it is **demoted** to working data and a fresh one is sealed first.

| Id | Seed | Contexts | Negatives | Sealed in | Chosen by | Status | Official numbers produced |
|---|---|---|---|---|---|---|---|
| H1 | 77031117 | 9 (TOML, XML, Markdown code block, Go, JS, X-Api-Key header, SQL, CLI flag, error message) | holdout half of committed negatives (137 files) + seeded synthetic | e084b24 | builder session (had seen rule code; contexts chosen before any Nacre rule existed) | **demoted 2026-09-30**: its JWT/sntryu_ failures were seen and are being fixed | ce5e315 evidence `A-0010-measurement-2026-09-30-holdout.md` (the numbers stand as H1 results; later numbers must not use H1) |
| H2 | 3671910561 | 16, chosen by the separate session (Java text block, raw HTTP POST without trailing newline, CI log, Python traceback, chat prose followed by a comma, Terraform HCL, shell prompt with `&&`, C# verbatim string, PHP array, Lua long bracket, Markdown table cell, HTTP GET query parameter, pytest assertion output, Ruby %q(), Kubernetes Secret block scalar, JSX template literal); documents embedded; every generator in every context | 150 fresh CPython 3.14.3 stdlib files (PSF) + seeded synthetic negatives | 633f637 | a **separate session that did not read detector code** (2026-09-30). The builder saw the context DESCRIPTIONS, not results, before writing the boundary fix; the fix follows the charset principle only | **official** | `A-0010-measurement-2026-09-30-H2.md`: catch 100% in every group; FP 3.25% as sealed: **INVALID BY CONSTRUCTION** (D-0011 amendment 8): the H2 build embedded synthetic negatives into credential slots, so they read `credential=<hex>`; real-code 0/150. H2 is not altered. The official FP comes from H3 |
| H3 | 1375181615 | 17, chosen by a separate session (7 labelled credential slots: Dockerfile ENV, Rust, notebook JSON, Swift, PowerShell no trailing newline, Ansible folded scalar, nginx header; 10 non-slot: git diff, Go table test, strace buffer, WebSocket frame, CSV last field, reST code block, Makefile recipe, Clojure no trailing newline, HTML data attribute, protobuf text); documents embedded, negatives never embedded, credential-slot category in slot contexts only, every generator in every context | 150 further CPython stdlib files, disjoint from H2 (PSF) + seeded raw synthetic negatives | 3700f1f | a **separate session that did not read detector code** (2026-09-30); sha256 `a4518c359e4da839a13ac91b21d150c84c9b13922c2d701e2eb2fbec58f22e0d` | **official** (D-0011 amendment 8: per-character caught gates from here) | `A-0010-measurement-2026-09-30-H3.md`: every provider/generic group 100% per-character (longest fragment 0); FP 0/400; public kept 100%; credential-slot category (ungated) 54–72%, flagged vs working 82–83% |
| H4 | 4074191310 (third-party negatives selection 4211584703; first draw 638022740 withdrawn) | 17, chosen by a separate session (9 labelled credential slots: Java .properties, systemd Environment=, Python requests Authorization header, .NET connection string Password=, docker login --password, Elixir heredoc, Go raw string, .netrc last bytes no newline, .npmrc _authToken; 8 non-slot: psql aligned output, LaTeX texttt, NASM db string, Python doctest output, Dart print last bytes no newline, XML CDATA, GraphQL id argument, printf piped to pbcopy); documents embedded, negatives never embedded, credential-slot category in slot contexts only (250 samples), every generator in every context | **150 real-code files**: all 128 remaining eligible CPython 3.14.3 stdlib files (PSF) + 22 permissively licensed third-party files (anyio 3 MIT, httpcore2 5 BSD-3, httpx2 2 BSD-3, idna 1 BSD-3, openai 5 Apache-2.0, pydantic 5 MIT, truststore 1 MIT; licences in `negatives_holdout4/LICENSES/`), disjoint by path and content from negatives/, negatives_external/, H2, H3 and the withdrawn first draw; + 250 seeded raw synthetic negatives (unchanged) | committed 2026-10-01 (with the redrawn negatives) | a **separate session that did not read detector code** (2026-10-01); total sha256 `4b573eef5a82f0f72f853b26a9d31e79dc1d005b2060c37d514f338a2333266d` (negatives manifest `d61c6e1b…fdaa`; the first-draw total `1cdc0d45…` is withdrawn) (corpus digest `ea4356ce…3637`; method in `HOLDOUT4_MANIFEST.json`); structure test `tests/ledger/test_holdout4_structure.py` 8/8, verified by the caller | **MEASURED ONCE 2026-10-02 (gate item 12 FAILED), then DEMOTED to working data (all categories)**. Purpose: the Phase 3 credential-slot target (owner 2026-10-01: catch ≥ 90%, FP ≤ 2%). Builder notes: slot 4 is a CLI flag (`--password value`), not key=value; context disjointness is checked on a 3-line window (an Elixir heredoc value line equals H3's Ansible value line on its own) | `A-0010-measurement-2026-10-02-H4.json` / `.md`: credential-slot 222/250 = **88.8% < 90% (FAIL)**; FP **0/400** (0%); every other group ≥ 99% except generic:dotenv/assignment 49/50 (98%) |

## Disclosure: H4 first-draw negatives were scanned by accident (2026-10-01)
- **What happened:**
  - while committing, this session staged H4's 150 stdlib negatives (selection seed 638022740) before
    `negatives_holdout4/` was registered in the pre-commit hook's manifest-skip table;
  - the hook ran the detector on them;
  - **one result was revealed to the rule-writing session:** a single entropy-layer flag on one negative file. No
    positive sample and no synthetic negative was exposed.
  - **No rule was written or changed in response.**
- **Owner decision (option b):**
  - a fresh separate session redraws only the stdlib negatives with a new seed, excluding the first draw;
  - `negatives_holdout4/` is added to the skip table;
  - the hook now **fails closed**: any staged file in an unregistered folder under `tests/ledger/secret_corpus/`
    refuses the commit before any detector runs.
- **Redraw done (2026-10-01, owner decision: all 128 eligible stdlib files topped up with permissive third-party
  files):**
  - **Built by:** a separate blind session.
  - **Real-secret pre-scan:** an independent tool (detect-secrets 1.5.0, Yelp; not Nacre's detector) over all
    533 candidates. 4 files had hits; each flagged line was reviewed by eye, none was a real credential, and none
    was excluded. The reasons are in `negatives_holdout4/MANIFEST.json`.
  - **Composition:** in the H4 row above.
- *Earlier status (superseded by the redraw):* **BLOCKED, owner decision needed.**
  - Under the current rules (CPython 3.14.3 stdlib, the same exclusions, disjoint from H2, H3 and the first draw),
    only **128** candidates remain, not 150.
  - The first draw's file names are kept in `HOLDOUT4_FIRST_DRAW_NEGATIVES.json` for exclusion.
  - H4 is not committed and not measured until the redraw is done.

## H3 demoted to working data for the credential-slot category (owner, 2026-10-02)
- **From now on, H3's credential-slot samples are working data.** The credential-slot rules are written against
  them and other working data. Its numbers for that category are no longer official.
- **H3 stays official for every other category** (provider and generic groups, public kept, FP), until a rule
  writer sees those failures.
- **The credential-slot gate is H4, measured exactly once** after the rule work: catch ≥ 90% **and** FP ≤ 2%,
  together.
- **No H4 baseline is taken before that** (owner, 2026-10-02), so H4 is never seen by the rule writer before it
  judges the rules.

## Credential-slot rule work (2026-10-02, on working data only)
- **New Nacre rule `credential-slot-value`** (`nacre-rules-v1.toml`; a tightening change under D-0011 amendment 4,
  with no allowlist).
  - **Match:** a key naming a credential, then only structural syntax, then a 16–128 character value containing a
    digit, with entropy > 3.0.
- **Working data** (working set + demoted H1 + H3, which is working for this category since 2026-10-02):
  - **748/750 caught per character.** Before the rule, H3's credential-slot rates were 54–72%.
  - **Working-negatives FP:** 0.37% → 0.49% (one Python type annotation whose type name contains a digit, in a cryptography signature).
  - **H3 (official) for every other category still passes**, including its FP bar (the holdout suite, pass/fail).
- **Narrowed once (repo-tree false positives):** the quoted key-value pair separator now stays on one line and
  takes only ' or " quotes (Markdown lists of rule names and multi-line JSON lists were matched).
- **Known trade-off:** a value with no digit is not taken. About 1.5% of random 24-character alphanumeric values
  have none; passphrases made only of letters are also missed.
- **H4 is READY to be measured once, as the gate** (catch ≥ 90% and FP ≤ 2%, together). Awaiting the owner's go.

## H4 gate measurement (2026-10-02, owner go; measured exactly ONCE)
- **Run:** `A-0010-measurement-2026-10-02-H4.json` (code 4842b82, Nacre rules sha256 `12395ce7…3e62`, 5.0 s).
- **Gate item 12: FAILED.**
  - **Credential-slot catch:** 222/250 = **88.8%**, below the 90% target. Per value kind: alnum-24 0.88, base64-32
    0.90, hex-40 0.88, hex-64 0.90, uuid 0.88.
  - **False positives on H4 negatives:** **0/400 = 0.0%** (target ≤ 2%: met). No false positives, so **no
    type-annotation pattern appeared**.
  - **Every other group:** ≥ 99% except **generic:dotenv/assignment 49/50 = 98%** (below its own 99% bar).
- **Owner pre-decision applied:** no rule change, no re-measurement on H4, **H4 demoted to working data (all
  categories, since its results have now been seen)**. A new sealed holdout (H5) is needed before gate item 12 can
  be measured again.
- **Misses, inspected only after the demotion:**
  - **25 of 28** are one context: a value in an Elixir triple-quoted heredoc (`private_key: """` ⏎ value ⏎ `"""`);
    the rule has no `"""`+newline separator;
  - **1:** a random alnum-24 value with no digit (the digit requirement's known gap, A-0044);
  - **2:** one character survived (Spring `.properties`, .NET connection string). Not diagnosed;
  - **the dotenv miss:** the generator's `CLIENT_SECRET=<value>` placed after `_authToken=` in the `.npmrc`
    context.

