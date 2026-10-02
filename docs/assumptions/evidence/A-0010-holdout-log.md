# A-0010 holdout-generation log (D-0011 amendments 1, 5)

Which holdout produced each official number. A holdout is **official** until a rule writer has seen its
failures and written a fix; then it is **demoted** to working data and a fresh one is sealed first.

| Id | Seed | Contexts | Negatives | Sealed in | Chosen by | Status | Official numbers produced |
|---|---|---|---|---|---|---|---|
| H1 | 77031117 | 9 (TOML, XML, Markdown code block, Go, JS, X-Api-Key header, SQL, CLI flag, error message) | holdout half of committed negatives (137 files) + seeded synthetic | e084b24 | builder session (had seen rule code; contexts chosen before any Nacre rule existed) | **demoted 2026-09-30**: its JWT/sntryu_ failures were seen and are being fixed | ce5e315 evidence `A-0010-measurement-2026-09-30-holdout.md` (the numbers stand as H1 results; later numbers must not use H1) |
| H2 | 3671910561 | 16, chosen by the separate session (Java text block, raw HTTP POST without trailing newline, CI log, Python traceback, chat prose followed by a comma, Terraform HCL, shell prompt with `&&`, C# verbatim string, PHP array, Lua long bracket, Markdown table cell, HTTP GET query parameter, pytest assertion output, Ruby %q(), Kubernetes Secret block scalar, JSX template literal); documents embedded; every generator in every context | 150 fresh CPython 3.14.3 stdlib files (PSF) + seeded synthetic negatives | 633f637 | a **separate session that did not read detector code** (2026-09-30). The builder saw the context DESCRIPTIONS, not results, before writing the boundary fix; the fix follows the charset principle only | **official** | `A-0010-measurement-2026-09-30-H2.md`: catch 100% in every group; FP 3.25% as sealed: **INVALID BY CONSTRUCTION** (D-0011 amendment 8): the H2 build embedded synthetic negatives into credential slots, so they read `credential=<hex>`; real-code 0/150. H2 is not altered. The official FP comes from H3 |
| H3 | 1375181615 | 17, chosen by a separate session (7 labelled credential slots: Dockerfile ENV, Rust, notebook JSON, Swift, PowerShell no trailing newline, Ansible folded scalar, nginx header; 10 non-slot: git diff, Go table test, strace buffer, WebSocket frame, CSV last field, reST code block, Makefile recipe, Clojure no trailing newline, HTML data attribute, protobuf text); documents embedded, negatives never embedded, credential-slot category in slot contexts only, every generator in every context | 150 further CPython stdlib files, disjoint from H2 (PSF) + seeded raw synthetic negatives | 3700f1f | a **separate session that did not read detector code** (2026-09-30); sha256 `a4518c359e4da839a13ac91b21d150c84c9b13922c2d701e2eb2fbec58f22e0d` | **official** (D-0011 amendment 8: per-character caught gates from here) | `A-0010-measurement-2026-09-30-H3.md`: every provider/generic group 100% per-character (longest fragment 0); FP 0/400; public kept 100%; credential-slot category (ungated) 54–72%, flagged vs working 82–83% |
| H4 | 4074191310 (third-party negatives selection 4211584703; first draw 638022740 withdrawn) | 17, chosen by a separate session (9 labelled credential slots: Java .properties, systemd Environment=, Python requests Authorization header, .NET connection string Password=, docker login --password, Elixir heredoc, Go raw string, .netrc last bytes no newline, .npmrc _authToken; 8 non-slot: psql aligned output, LaTeX texttt, NASM db string, Python doctest output, Dart print last bytes no newline, XML CDATA, GraphQL id argument, printf piped to pbcopy); documents embedded, negatives never embedded, credential-slot category in slot contexts only (250 samples), every generator in every context | **150 real-code files**: all 128 remaining eligible CPython 3.14.3 stdlib files (PSF) + 22 permissively licensed third-party files (anyio 3 MIT, httpcore2 5 BSD-3, httpx2 2 BSD-3, idna 1 BSD-3, openai 5 Apache-2.0, pydantic 5 MIT, truststore 1 MIT; licences in `negatives_holdout4/LICENSES/`), disjoint by path and content from negatives/, negatives_external/, H2, H3 and the withdrawn first draw; + 250 seeded raw synthetic negatives (unchanged) | committed 2026-10-01 (with the redrawn negatives) | a **separate session that did not read detector code** (2026-10-01); total sha256 `4b573eef5a82f0f72f853b26a9d31e79dc1d005b2060c37d514f338a2333266d` (negatives manifest `d61c6e1b…fdaa`; the first-draw total `1cdc0d45…` is withdrawn) (corpus digest `ea4356ce…3637`; method in `HOLDOUT4_MANIFEST.json`); structure test `tests/ledger/test_holdout4_structure.py` 8/8, verified by the caller | **MEASURED ONCE 2026-10-02 (gate item 12 FAILED), then DEMOTED to working data (all categories)**. Purpose: the Phase 3 credential-slot target (owner 2026-10-01: catch ≥ 90%, FP ≤ 2%). Builder notes: slot 4 is a CLI flag (`--password value`), not key=value; context disjointness is checked on a 3-line window (an Elixir heredoc value line equals H3's Ansible value line on its own) | `A-0010-measurement-2026-10-02-H4.json` / `.md`: credential-slot 222/250 = **88.8% < 90% (FAIL)**; FP **0/400** (0%); every other group ≥ 99% except generic:dotenv/assignment 49/50 (98%) |
| H5 | 615367920 (negatives selection 4113351651) | 22, chosen by a separate session: 15 credential slots, of which 12 are multi-line literals (Bash quoted heredoc via read -d, Python triple single quotes, PowerShell single-quoted here-string, Kotlin raw string, Swift multi-line, TOML multi-line literal, C++ raw string with delimiter, Perl indented heredoc, Nix indented string, PHP nowdoc, YAML block scalar with indentation indicator, .NET web.config multi-line attribute) and 3 single-line (.pypirc password, C# options.ClientSecret, redis-cli AUTH); 7 non-slot (Fortran, Erlang, R, Haskell, Gherkin, and 2 multi-line literals naming no credential) | 150 permissively licensed third-party files from 10 distributions (anyio, fsspec, huggingface_hub, pydantic, httpcore, PyYAML, filelock, click, h11, typing-inspection; MIT / BSD-3 / Apache-2.0; ≤ 15% each), disjoint by path and content from every earlier set; detect-secrets 1.5.0 pre-scan: 21 hits in 14 files, all reviewed, none real, 0 excluded; + 250 seeded synthetic negatives | committed 2026-10-02 | a **separate session that did not read detector code** (2026-10-02); total sha256 `153a6858db68318e899f5657c967b4fc129a3089573c5c54666472b172cd48b5`; structure test 9/9, verified by the caller | **MEASURED ONCE 2026-10-02 (gate item 12 FAILED), then DEMOTED to working data.** Rules frozen at de7f23a. Disclosure: the builder's report described the contexts in words to the rule-writing session AFTER the rules were committed; no rule is changed before the single measurement | `A-0010-measurement-2026-10-02-H5.json` / `.md`: credential-slot 185/250 = **74.0% < 90% (FAIL)**; FP **2/400 = 0.5%** (met; url-userinfo-password, no type annotations); every other group ≥ 99% |
| H6 | 2441296908 (negatives selection 471139560) | 30, chosen by a separate session that reported COUNTS ONLY: 22 credential slots (13 common / 9 long-tail, classified before building from the Stack Overflow Developer Survey 2025, GitHub Octoverse 2025 and the CNCF Annual Survey; every owner-listed common format covered; 5 command-line, 4 attribute-pair, 5 heredoc/multi-line) and 8 non-slot (2 with benign high-entropy values) | 150 permissively licensed third-party files from 15 distributions (≤ 15% each), disjoint by path and content from every earlier set; detect-secrets 1.5.0 pre-scan: 13 hits in 9 files, all reviewed, none real, 0 excluded; + 250 seeded synthetic negatives | seal record committed 2026-10-02 BEFORE measuring (file hashes below); files committed after the single measurement | a **separate session that did not read detector code** (2026-10-02); total sha256 `f272bb5db331030d715752b3ceb38f6728a626614d07dfb161162f78fd63272e`; structure test 11/11, verified by the caller | **MEASURED ONCE 2026-10-02: revised gate item 12 FAILED (no H7, pre-decided).** Rules frozen at c4bb683 | `A-0010-measurement-2026-10-02-H6.json` / `.md`: common 134/160 = **83.8% < 95% (FAIL)**; long-tail 42/90 = **46.7% < 80% (FAIL)**; FP 4/400 = **1.0%** (met); every other group ≥ 99% |

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


## Rule work after the H4 failure (2026-10-02, working data only; H4 is working data now)
- **Root cause of the 2 single-character exposures** (Spring `.properties`, .NET connection string):
  - `credential-slot-value` had an optional string-prefix group (`b`/`r`/`f`/`u`, for `b"…"` literals) that could
    match WITHOUT a following quote, so it swallowed the first character of an unquoted value starting with one of
    those letters.
  - **Fix:** a prefix is taken only together with its quote.
  - **Shared by other rules?** Probed every layer with values starting with each letter and digit across 16
    contexts. Only this rule lost a first character; gitleaks `generic-api-key` covered the whole value wherever
    it overlapped.
  - **Separate entropy-layer quirk found:** after a cue word, its value class included `=`, so it redacted a
    leading `=` too (over-coverage, not exposure). Fixed by the next item.
- **Nested dotenv miss:**
  - **Cause:** the entropy layer's candidate started at the first `:`/`=` and took `_authToken=CLIENT_SECRET=value`
    as one 73-character token, over the 64 limit, so it was skipped; the scan does not overlap.
  - **Fix (general):** a candidate holding an internal `=` (not trailing padding) is judged on its last
    right-hand side.
- **Multi-line literals:** the slot rule now takes a credential-named key followed by a multi-line string literal,
  heredoc or block scalar whose content starts on a later line.
  - The openers come from 20 language references (Python, Kotlin, Java, Scala, Swift, Elixir, Ruby, Bash, HCL,
    PHP, PowerShell, C#, Rust, C++, Go/JS, TOML, Dart, Lua, Nix, YAML, plus XML CDATA). The sources are listed in
    `scripts/build_credential_slot_regex.py`, which generates the rule's regex.
- **Working data after the revision:**
  - **Credential-slot catch:** working set 499/500; H3 250/250; H4 249/250. The remaining miss is the digit gap,
    A-0044.
  - **Every other H4 group** is ≥ 99% (the dotenv case is fixed).
  - **False positives:** H4 negatives 0/400; working negatives 4/811 = 0.49% (unchanged).
- **Next:** H5, built blind by a separate session (multi-line contexts in languages the rule writer did not
  target), sealed, then measured once. If it fails, there is no patching on H5.
- **H5 commit (2026-10-02):** the pre-commit hook flagged two file-path strings in H5's own manifests (entropy
  layer: `PyYAML-6.0.3/yaml/constructor.py` and `…/tokens.py`). These are repository metadata, not H5 sample
  documents; the negatives themselves are skipped by manifest hash. Both are on the hook's exact-value repo allowlist,
  scoped to those two files, with a reason. No detector rule changed.

## H5 gate measurement (2026-10-02; measured exactly ONCE on rules frozen at de7f23a; report at commit 98bf5dc)
- **Gate item 12: FAILED again.**
  - **Credential-slot catch:** 185/250 = **74.0%** (0.74 for every value kind).
  - **False positives:** **2/400 = 0.5%** (met). Both are `url-userinfo-password` on docstring URL examples in
    fsspec (`implementations/gist.py`, `utils.py`). **No type-annotation pattern.**
  - **Every other group:** ≥ 99%.
- **Owner pre-decision applied:** no patching on H5; **H5 demoted to working data.**
- **The 65 misses are 4 slot contexts, each missed entirely (0 partial):**
  1. Bash `read -r -d '' DEPLOY_TOKEN <<'EOF' || true`: a heredoc after a SPACE (no assignment), with trailing code
     on the opener line (20);
  2. Perl `my $db_password = <<~"END";`: the opener line ends in `;` (the rule allows only whitespace or a method
     chain) (15);
  3. .NET `<add key="Webhook:SigningKey" value="` (multi-line attribute): the credential name is the VALUE of a
     `key` attribute, and the secret sits in a sibling `value` attribute (15);
  4. redis-cli `AUTH default <value>`: a command, then a username, then the password (an intervening word) (15).
- **The 12 multi-line contexts:** 9 were caught in full (Python, PowerShell, Kotlin, Swift, TOML, C++ raw, Nix,
  PHP nowdoc, YAML), so the generalisation from the language references held where the key-then-opener shape
  applied. All four failures are shapes outside it.


## PRE-REGISTRATION: gate item 12, revised (owner, 2026-10-02). Committed BEFORE H6 is built or any H6 context is chosen
- **New approach (owner):** replace shape enumeration with an additive **proximity rule**.
  - **It takes:** a high-entropy, digit-bearing token (16–128 characters, entropy > 3.0) that appears after a
    credential word in the same statement, or on the first non-empty line after a string or heredoc opener,
    whatever the syntax between them.
  - The existing `credential-slot-value` rule is kept.
  - The rule is measured on working data (working set, H3, H4, H5) before H6 is measured.
- **Targets (all three must hold; measured ONCE on H6):**

  | Metric | Target |
  |---|---|
  | Credential-slot catch, per character, pooled over H6's **common** contexts | **≥ 95%** |
  | Credential-slot catch, per character, pooled over H6's **long-tail** contexts | **≥ 80%** |
  | False positives on H6 negatives (document level, as A-0010) | **≤ 2%** |

- **Common contexts (owner list):** .env, YAML, JSON, TOML, .properties, Dockerfile, Kubernetes manifests, CLI
  flags, connection strings.
  - The H6 builder classifies EACH slot context as common or long-tail **before building**, from public prevalence
    data, documented in `holdout_6.py` with its sources.
  - Owner-listed formats are common; anything else needs prevalence evidence to count as common.
- **H6 contents:** built and sealed by a separate blind session. It includes command-line, attribute-pair and
  heredoc shapes.
- **Pre-decided (owner):** a pass closes gate item 12. A fail means **no H7**: the result goes to the owner.
- **Proximity layer built (2026-10-02; working data only; H6 still being built blind).**
  - **What:** `ledger/find_credential_proximity.py`, layer 4 of `strip_secrets`. A code layer, not a TOML regex: a
    2.3 kB regex exhausted RE2's DFA.
  - **Tightened on working data:**
    - a token containing a credential word is an identifier (cryptography class names such as
      `X25519PrivateKey`);
    - a contiguous alphabet run is a constant;
    - compound key names (signing, encryption, master, session, HMAC, shared, webhook key) added to the
      credential words;
    - the opener search continues past an opener whose next line holds no value.
  - **Working data:** credential-slot catch working set 499/500, H3 250/250, H4 249/250, H5 249/250; the 3
    remaining misses are digit-free values (A-0044).
  - **False positives:** 7/1611 = 0.43% over the working, H4 and H5 negatives with synthetic negatives.
- **Repository false positives found before H6 (2026-10-02, still working data).**
  - **The trigger:** the first proximity version flagged **208 of the repository's 2,106 text files** (1,672
    findings), mostly recorded model-call results and evidence JSON.
    - The working negatives (library source code) had not shown this.
    - Agent data looks more like these JSON and log records than like library code.
  - **Tightenings, all applied before H6 is measured:**
    - the credential word must be a whole identifier component (not inside `authoritative`, `tokens`,
      `tokenizer`);
    - the value must be in the word's own field (no `, "` or brace between);
    - nearest label wins (`span_sha256=<hex>` belongs to `span_sha256`);
    - a lowercase path is a name;
    - a token right after `_` or `.` is an identifier tail.
  - **Also fixed:** time was quadratic on large inputs (320 kB took 18.8 s; the full suite stalled); now linear,
    about 1.3 s for 1.2 MB.
  - **After:** 0 findings in the repository; working credential-slot catch unchanged (99.6–100%); working FP
    0.37%.
  - **Not covered by the pre-registered FP target:** the H6 FP target is measured on code negatives. The
    repository result shows that JSON/log-style data is a separate FP risk.

## H6 seal record (2026-10-02, committed BEFORE the single measurement)
- **Rules:** frozen at c4bb683 (slot rule plus the hardened proximity layer).
- **H6 files (sha256), paths relative to the corpus folder under `tests/ledger/`:**
  - `holdout_6.py` sha256 `ba8ce058d7034b521b7da1394708e5b87eb29c9f533b40b2522927e4ad4b4887`
  - `build_negatives_holdout6.py` sha256 `bf060b2ab8a50671265b19a0f810ad643e01e718c237fc16e1520ac6dc8e3f70`
  - `HOLDOUT6_MANIFEST.json` sha256 `6da20eb84173e0816b4552c89afc4c596722b6800aebb7894270e9eb4f4c1d0e`
  - `negatives_holdout6/MANIFEST.json` sha256 `1970a5ae648ece54ad232c6601400ec76126798522dd93adb4e4edb6b51377a6`
  - `../test_holdout6_structure.py` sha256 `0b34cef5f743297bed6b45e765712d29af4d9b7465fb2d6d1091c8918cb233d1`
- **Why the files themselves are not in this commit:**
  - while staging, a pre-scan of `holdout_6.py` (the definition file) showed 2 template lines matching
    `url-userinfo-password` (file, line and rule only, no values).
  - That reveals only that some context has a URL-with-userinfo shape; the rules were already frozen.
  - Inspecting those lines to restructure or allowlist them would unblind H6, so the files are committed after the
    measurement, with exact-value repo allowlist entries for the 2 template lines.
  - These hashes prove the set measured is the set sealed here.

## H6 gate measurement (2026-10-02; measured exactly ONCE at affd522 on rules frozen at c4bb683; sealed file hashes verified)
- **Revised gate item 12: FAILED.**
  - **Common contexts:** 134/160 = **83.8%** (target ≥ 95%).
  - **Long-tail contexts:** 42/90 = **46.7%** (target ≥ 80%).
  - **False positives:** 4/400 = **1.0%** (met). One is from the proximity layer (`openai/lib/azure.py`); three
    are from older rules.
  - **Every other group:** ≥ 99%.
- **Pre-decided (owner):** a fail means **no H7**; the result goes to the owner. No rule has been changed.
- **Miss analysis** (after the measurement; 74 misses in 8 of 22 slot contexts):
  1. **Attribute pairs killed by the FP tightenings added for the repository's JSON** ("nearest label wins",
     "same field"): a Postman-style JSON key/value pair (11/15 missed, common) and an HCL `{ name =
     "smtp_password", value = … }` (8/10, long-tail).
  2. **The credential word on an EARLIER line with no opener between:** a Kubernetes env `- name:
     GRAFANA_ADMIN_PASSWORD` then `value:` (8/10, common), and an HCL heredoc three lines below a sensitive
     variable (10/10, long-tail).
  3. **Vocabulary:** OpenSSL `-passout pass:<value>` (`pass` is not a credential word; 7/10, common), and
     `htpasswd … admin <value>` (`passwd` inside `htpasswd` fails the whole-word rule; 10/10, long-tail).
  4. **No credential word at all:** `rabbitmqctl add_user ingest <value>` and IMAP `LOGIN user "<value>"`
     (20/20, long-tail); out of a proximity rule's reach by design.
- **The trade-off this exposes:** the five tightenings that took the repository's own JSON/docs from 208 files to
  0 cost the attribute-pair shapes. Those tightenings were made on working data before H6, as allowed, but they
  moved the rule away from the owner's "regardless of syntax" specification.
- **Hook:** `holdout_6.py` holds 2 template lines (its docstring and an f-string: a MongoDB SRV connection URL whose userinfo password is the template placeholder) that
  `url-userinfo-password` flags. Exact-value repo allowlist entries (`value`, `{v}`), scoped to that file, were
  added after the measurement.

