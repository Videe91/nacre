# Current state

**Phase:** 2 COMPLETE (tag `phase-2-complete`, owner-accepted 2026-10-01); Phase 3 — recall, interface (PLAN APPROVED 2026-10-01; freezing sets before any recall code)
**Last updated:** 2026-10-01

**Phase 3 status (2026-10-01):** the plan is ready for owner review in `docs/plans/phase-3-plan.md`.
- **Proposed ADRs:** D-0024 (embeddings and search, D3), D-0025 (recall pipeline, ContextFrame and trace),
  D-0026 (interface and auth, D3), D-0027 (binary attachment scanning, D3), D-0028 (Anthropic adapter).
- **Other documents:** the EXP-0004 pre-registration DRAFT; assumptions A-0032 … A-0043; INDEX rows R1–R26
  (planned).
- **Evidence:** the encrypted index is feasible (A-0032 evidence).
- **APPROVED by the owner 2026-10-01 with changes** (recorded in D-0024 … D-0028, EXP-0004 and the plan). D-0024 …
  D-0028 accepted. EXP-0004 pre-registered with every number fixed.
- **Next, in order:** build and freeze the EXP-0004, H4 and I1 sets in separate sessions; then recall code.

## Done
- Repo constitution, rules, registers, structure checker.
- Final build spec in `docs/spec/SPEC.md`; updated to match D-0002 … D-0006 (all four accepted
  departures, plus the time concepts and key hierarchy).
- ADRs accepted by the owner: D-0002 (envelope, incl. MNEXA ADR-0010 time concepts),
  D-0003 (seal chain + checkpoints), D-0004 (key hierarchy, D3), D-0005 (scope isolation, D3),
  D-0006 (dependencies + test infra).
- Assumptions A-0007 … A-0016 recorded (A-0007 provisional target; A-0010 owner thresholds).

## Next
Build the Phase 1 files in `docs/modules/INDEX.md` order, one functionality + test per step,
committing and pushing after each.
- Done: #1 `core/event.py`, #2 `core/db.py`, #2a `core/blob_store.py`, #2b `core/root_key_provider.py`,
  #3 `schema/apply_migrations.py` + SQL 0001–0003, #2c `core/encode_cbor.py`, #2d `core/decode_cbor.py`, #4 `ledger/encode_envelope.py`, #5 `ledger/seal_event.py`, #3d `sql/0004_checkpoints.sql`.
- **#6 CLOSED 2026-09-30** (H3 official). **#14 built; A-0007 measured; #7, #7a, #8 built.** #15/#15a/#15b, #16, #17, #18, #19 built. #20 (requests), #20c (execution), #20d (keyadmin tx) built; #21 folded into them. #20b master rotation and #20a root rotation built; **A-0008 validated** (pg_dump backup recovery, crash/resume). D-0015 accepted; #15c orphan collection built. **Every Phase 1 functionality in INDEX is done, and the Phase 1 gate was run in full on 2026-09-30 (all 6 items pass, below).** **Phase 1 gate ACCEPTED by the owner (2026-09-30); tagged `phase-1-complete`.** **Phase 2 planning done (2026-10-01). Owner answers received the same day (MNEXA = prior art; D3 parts approved). Next: EXP-0001 re-baseline, run by the owner, results to the owner BEFORE any porting.** Earlier status: `docs/plans/phase-2-mnexa-port-inventory.md`, proposed D-0016 … D-0022, A-0023 … A-0028, INDEX rows P1–P34, and the proposed gate below. The MNEXA frozen suite is already frozen in `tests/regression/mnexa/` (data only). **No Phase 2 code until approval.**
- To run DB tests: `docker compose up -d --wait`, then `pytest`.
- **Owner tool:** `scripts/measure_token_format.py` measures real tokens' prefix/length/charset without
  printing them (D-0007 amendment 4). Run locally; paste only its suggested ASSUMPTIONS row back.
- **Every clone:** `git config core.hooksPath scripts/hooks` (D-0010 pre-commit secret scan; needs `.venv`
  with `google-re2`). Temporary scanner `scripts/scan_staged_secrets.py` is replaced by #6.

## Phase 1 gate (FROZEN by owner 2026-09-30)
Phase 1 is done when all of these pass on the Docker Postgres (`postgres:17.11`):
1. Idempotent retries return the original commit, with no duplicates.
2. AS_OF(N) snapshots are stable (MNEXA ADR-0010 R-18: byte-identical after later commits).
   *Status: tested through read_stream (#16): snapshot at N identical after 10 later commits; beyond-head refused.*
3. The chain verifier detects tampering, including a full-stream rewrite (caught by checkpoints).
   *Status: tested (#19): edited ciphertext, edited header, deleted row, reordered rows, and a full rewrite with
   recomputed seals caught only by the signed checkpoint.*
4. Shredding makes payloads unreadable while the chain still verifies.
   *Status: tested end to end (#20c): after an executed erasure, payloads are Shredded and verify_chain passes.
   Finality (A-0008) validated: key rows recovered from a real pg_dump cannot be unwrapped after master and root
   rotation.*
5. The cross-scope read test fails as expected (D-0005 S-3). *Status: suite in place and passing through the
   door (#10: 25 kind pairs + reuse/rollback/revoke). Re-run at the gate with real appended events (encrypted
   bodies + attachments via append_event/read_stream/read_attachment): reads refused, and a write into another
   scope is refused by RLS in the database.*
6. A-0007 throughput is measured and the result recorded (pass/fail against the provisional target is reported, not hidden).
   *Status: MET, POOLED (psycopg_pool, 2026-09-30): p99 < 50 ms at ≤ 4 writers per stream (worst 24.0 ms);
   16-writer stress p99 ≤ 150 ms (worst 87.8 ms). **Official evidence = the pooled runs** (production setup): e3206cf,
   confirmed at the gate run (4 writers p99 ≤ 22.7 ms, stress 93.3 ms). Unpooled tables are history only.
   Evidence: `docs/assumptions/evidence/A-0007-append-throughput-2026-09-30.md`; `pytest -m bench` (pooled).*

## Phase 2 gate (FINAL, owner 2026-10-01; rebuild, not port; the bar is pre-registered in EXP-0003)
Phase 2 is done when all of these pass on the Docker Postgres:
1. **Frozen-suite integrity:** every file in `tests/regression/mnexa/` and `tests/regression/exp0001/` matches its
   manifest.
2. **Instrument validation:** the ported grader reproduces MNEXA's recorded verdicts on the stored decisions of
   003–016 exactly (2,070 verdicts).
3. **EXP-0003 bar (live, owner-run):**
   - **Setup:** tasks 014–016, k = 3, `gpt-4o-mini-2024-07-18`, no-memory control in the same runs.
   - **Nacre ≥ 162/180** pooled and **≥ 51/60** per set.
   - **Nacre − no-memory ≥ 50 points.**
   - **Zero** on all six safety metrics. **No reruns.**
   - EXP-0001 (MNEXA 178/180) is reported alongside; it is not a gate.
4. **Recorded-mode determinism:** replaying the EXP-0003 recording reproduces every grade exactly with the network
   blocked.
5. **Belief lifecycle:** quorum promotion, **single-source promotion with its safeguards (D-0017 amendment 1,
   one test per condition)**, versioning and upgrade, contradiction, contestation and supersession, and deviations
   1–4.
6. **Episodes:** each MNEXA ADR-0009 invariant (Q-1…Q-15) has a passing test.
7. **Write gate:**
   - **Recall:** 100% flag recall on the lesson-bearing episodes of 003–016 through the real capture mapping; an
     authoritative correction always flags (D-0019 R1, R4).
   - **Selectivity (owner, R4):** on the blind frozen set `routine_episodes_v2`: routine flag rate ≤ 5%, and ALL 75
     adversarial episodes (vague, mismatched, late) flagged. **PASSING: 0/200 routine, 0/75 missed.** v1 is
     historical.
   - **Canary:** a flag-everything gate must fail this item (passing).
8. **Rebuild:** the `interp` projection rebuilt from the ledger is identical. **PASSED on real state**: 180/180
   EXP-0003 scopes in an offline recorded replay (`tests/regression/exp0003_item8/`).
9. **Shredding and privacy (D-0023 contributor-set keys built 2026-10-01):**
   - erasing a scope or person makes its model-call recordings, proposals and beliefs unreadable;
   - no content plaintext in the projection;
   - SI-1 to SI-7 pass.
10. **Every model call records** the pinned version, timeout and retry settings, and token cost (D-0021 amendment
    1). Cost per episode and per task is reported.
11. **Anti-shortcut guard (i):** product code (`src/nacre/` outside `eval/`) never sees task data and never parses
    role markers.
12. **Anti-shortcut guard (ii):** injected safety challenges never reach memory.
13. **Anti-shortcut guard (iii):** if every outcome section is untrusted, nothing is admitted.
14. **Phase 1 gate still passes.**

**Removed (owner):** mechanism parity, prompt parity and recorded formation parity (old R2/R3), and the margins
against EXP-0001. **Not in the gate:** the checker model (its own experiment later).

## Standing instructions (owner, 2026-09-30)
- **Models (owner, 2026-10-01):** always pin dated model versions. On a successor, re-baseline both MNEXA's harness
  and the no-memory control before any comparison.
- **A-0007 / gate item 6:** the throughput test must run with the **production connection setup**.
  Decide on `psycopg_pool` (new dependency, needs an ADR) **before** gate item 6 runs; otherwise
  the result is reported explicitly as **unpooled**.
- **A-0017 / INDEX #6:** when #6 starts, evaluate `google-re2` against Python `re`: identical
  semantics to gitleaks' rules, and no catastrophic backtracking on untrusted input. Bring the
  comparison to the owner as part of A-0017.

## Future-phase requirements (owner)
- **The 014–016 suite is SATURATED (owner, 2026-10-01).** Nacre scored 180/180 in EXP-0003. With one episode and
  one isolated scope per family, memory is the verbatim authoritative correction, so the suite cannot measure further
  gains. It stays as a regression guard. **Phase 5 needs harder, real-world tasks:** interference between many
  memories in one scope, paraphrased or partial corrections, changing facts, and multi-week tracks (SPEC proof
  tier 3).
- **Phase 5: no learning from evaluation epochs.** When evaluation epochs exist, nothing captured inside one may be
  flagged, consolidated, promoted or formed into an episode (MNEXA ADR-0004 K-4, ADR-0009 Q-9).

## Open questions
- **Later (owner, 2026-10-01):** events ABOUT a person written by others (D-0023 decision 1). Today they stay on the
  writer's or system subject unless a caller names the person subject.
- **RESOLVED 2026-10-01 by D-0023 (built and tested): Phase 2 gate item 9, erasing a PERSON did not erase derived
  copies.**
  - **What happens:** model-call records, lesson proposals and belief versions are encrypted under the STREAM's
    (system-subject) key. After a person's data keys are destroyed, their original statement is unreadable, but a
    model-call record of a prompt built from it still holds their words, readable (proved by a test, then removed;
    see the log).
  - **What is fine:** scope erasure (SI-7).
  - **Options:**
    - (a) derived records are encrypted under a DERIVED key registered against every contributing subject; erasing
      any of those subjects destroys it;
    - (b) cascade on erasure: append replacements and rebuild a projection generation. This fails for the ledger
      copies, which are append-only;
    - (c) keep person content out of model prompts and memory.
  - **Recommendation: (a).** → Owner approved (a) in principle; **D-0023 proposed (D3), awaiting approval before
    any build.**
- **RESOLVED 2026-10-01 (D-0017 amendment 2: rebuild into a new generation, switch atomically, keep the old):** D-0017 text vs its own tables: D-0017 says rebuild "drops the scope's projection rows and
  replays", but the same ADR makes them append-only. Implemented as recompute-and-compare (gate item 8 checks
  identity). Owner: confirm, or amend D-0017's wording.
- **ACCEPTED for now (owner 2026-10-01): ADR-0009 Q-9 epoch half.** Recorded as a Phase 5 requirement (below).
- **RESOLVED 2026-10-01 (D-0017 amendment 1, option (a) with safeguards): single-episode promotion.**
  - **The gap:** D-0017 promotes only on a quorum of ≥ 2 distinct decisions. Every frozen family has exactly one
    failed episode, so under D-0017 Nacre would promote nothing and the EXP-0003 transfer would see empty memory.
  - **Options:**
    - (a) a lesson grounded in an **authoritative correction** (trusted reviewer, CI or person; origin `stated`) may
      promote at quorum 1, while lessons inferred from outcomes keep quorum 2;
    - (b) recall may show unpromoted proposals, marked as such;
    - (c) keep quorum 2 and accept that EXP-0003 measures nothing until a second episode exists.
  - **Recommendation: (a)** (D-0017 amendment). Owner decision needed.
- **Phase 3, decide before recall is built (owner, 2026-10-01):** embeddings are content-derived and partly
  invertible. They must be scoped and shreddable, so an ADR is needed before any embedding index exists (noted in
  D-0017).
- Resolved 2026-09-30: text attachments stripped, decided by CONTENT (valid, >= 95% printable UTF-8), not media type; binaries marked unscanned (D-0008 amendment 6).
- Resolved 2026-09-30:
  - D-0013 accepted: attachments are written before commit and fingerprint-verified on every read; checkpoints
    carry the key version; A-0020 added.
  - D-0014 accepted: 7-day cancellable grace; weekly root rotation; grace + rotation ≤ 30 days; self-erasure is
    automatic unless an org admin places a time-limited legal hold.
  - psycopg_pool adopted (D-0006 amendment 1).
- Resolved 2026-09-30: A-0007 rescoped (≤ 4 writers p99 < 50 ms; 16-writer stress ceiling 150 ms), met unpooled; A-0019 added (remedy = group commit).
- Resolved 2026-09-30: D-0012 accepted with owner corrections (trust by source + author, trust_basis, key rules, original-erased error); D-0002 amendment 4 (envelope v2).
- **PHASE 3 GATE ITEM (owner, 2026-09-30): scan binary attachments.** Extract text from binaries (unpack archives,
  PDF text, OCR for images) before storage; if a secret is found, reject the attachment with a clear error.
  Until then, binary attachments are stored as given and marked scan="unscanned" (A-0021, D-0008 amendment 6).
- **PHASE 3 GATE ITEM (owner, 2026-09-30): credential-slot target.** Before real agent data flows through the
  interface, set a target for "random value in a credential slot" (H3: 54–72%) and raise it, measured on a
  fresh H4 sealed under the same protocol. Not blocking Phase 1.
  **Target fixed by the owner 2026-10-01: credential-slot catch ≥ 90%, with false positives ≤ 2% on the H4 holdout.**
- **D-0011 accepted** (holdout; additive only; admission criteria; loosening needs owner approval).
- **Upstream (not blocking):** once proven on the holdout, prepare as gitleaks contributions (D-0011
  amendment 3): the `ghs_` stateless rule, the password-in-URL rule, and the **gitlab-pat-routable gap**
  (gitleaks' rule expects one dot; GitLab's current RoutableToken format has two).
- Resolved 2026-09-30: FP definition, credential-slot category, per-character "caught" (D-0011 amendment 8). H3 to be sealed; official FP from H3.
- Resolved 2026-09-30: `[...]`/`{...}` placeholder loosening DECLINED (D-0011 amendment 7).
- **Pre-commit hook switched to strip_secrets (D-0010).** The temporary gitleaks-only engine is gone;
  the hook keeps the reviewed repo allowlist + manifest-gated negatives skip.
  - **Generated now** (fully documented by the provider): GitHub classic + ghs stateless; GitLab legacy +
    routable (exact CRC); PyPI (V2 macaroon); Heroku HRKU-UUID form; Supabase sb_secret / sb_publishable
    (exact checksum), legacy service_role / anon JWTs; Sentry sntryu_ / sntrys_, DSN public vs legacy.
  - **Waiting for owner-measured facts** (one documented fact missing):
    - Cloudflare cfk_ / cfut_ / cfat_: charset and checksum length.
    - Cloudflare cfast_: checksum charset.
    - Replicate r8_: charset.
    - Heroku 65-char form: charset.
    - SendGrid SG.: part lengths and charset.
    - Shopify shpat_ / shpca_ / shppa_ / shpss_: charset. The only "hex" statement is a Shopify CLI comment
      that cites gitleaks, which fails the independence rule.
  - **Prefix-only**, SDKs checked and no length or charset validation found: Stripe, Slack, Anthropic,
    OpenAI admin, Hugging Face, DigitalOcean, npm, Docker Hub, Vercel, Netlify, Notion, GitHub fine-grained,
    Google AIza, AWS, Datadog. Useful SDK facts:
    - Stripe CLI: ≥ 12 chars, three '_' parts, extra prefix `rkcs`.
    - npm redaction: npm_/npms_ + 36–48 alnum.
    - Vercel CLI: token charset [A-Za-z0-9_].
    - AWS models: key ID 16–128 word chars.
    - Datadog agent scrubber: 32-hex API keys, 40-hex app keys.

    All of these need the measuring script (owner).
  - **GitHub checksum gap:** GitHub does not publish the CRC32 input bytes or the Base62 alphabet order,
    so the corpus uses 6 random base62 chars. This does not affect catch rates (rules don't validate
    checksums).
  - **Undocumented prefixes seen in provider code** (not generated): cfoat_, sk-ant-req-, ek_, rkcs, uk_,
    sntrya_, sntryi_.
- Resolved 2026-09-30: public-credential tag = optional encrypted body key (D-0008 amendment 5); owner-measured examples for the generic layer only; the seven undocumented prefixes are prefix-only (D-0007 amendment 5).
- **#6 provider coverage, state after verification (evidence/A-0010-format-verification.md).**
  - **Generated now** (fully documented by the provider): GitHub classic + ghs stateless; GitLab legacy +
    routable (exact CRC); PyPI (V2 macaroon); Heroku HRKU-UUID form; Supabase sb_secret / sb_publishable
    (exact checksum), legacy service_role / anon JWTs; Sentry sntryu_ / sntrys_, DSN public vs legacy.
  - **Waiting for owner-measured facts** (one documented fact missing):
    - Cloudflare cfk_ / cfut_ / cfat_: charset and checksum length.
    - Cloudflare cfast_: checksum charset.
    - Replicate r8_: charset.
    - Heroku 65-char form: charset.
    - SendGrid SG.: part lengths and charset.
    - Shopify shpat_ / shpca_ / shppa_ / shpss_: charset. The only "hex" statement is a Shopify CLI comment
      that cites gitleaks, which fails the independence rule.
  - **Prefix-only**, SDKs checked and no length or charset validation found: Stripe, Slack, Anthropic,
    OpenAI admin, Hugging Face, DigitalOcean, npm, Docker Hub, Vercel, Netlify, Notion, GitHub fine-grained,
    Google AIza, AWS, Datadog. Useful SDK facts:
    - Stripe CLI: ≥ 12 chars, three '_' parts, extra prefix `rkcs`.
    - npm redaction: npm_/npms_ + 36–48 alnum.
    - Vercel CLI: token charset [A-Za-z0-9_].
    - AWS models: key ID 16–128 word chars.
    - Datadog agent scrubber: 32-hex API keys, 40-hex app keys.

    All of these need the measuring script (owner).
  - **GitHub checksum gap:** GitHub does not publish the CRC32 input bytes or the Base62 alphabet order,
    so the corpus uses 6 random base62 chars. This does not affect catch rates (rules don't validate
    checksums).
  - **Undocumented prefixes seen in provider code** (not generated): cfoat_, sk-ant-req-, ek_, rkcs, uk_,
    sntrya_, sntryi_.
- **Where the "public credential" tag lives (proposal; needs owner approval, D2).** D-0007 amendment 4 tags
  public values in metadata, but D-0008 fixes the body map's v1 keys. Proposal: a D-0008 amendment
  adding one optional body key, `public_credentials`: an array of `{kind, span}`, encrypted like the
  rest of the body, so which public values an event holds is itself not plaintext. Until approved,
  strip_secrets reports public credentials in its return value only.
- **Shape source for "realistic examples" of no-doc providers (proposal).** Use the same owner-run
  measuring script (facts only, recorded as assumptions); providers never measured stay out of the
  generic-layer measurement and are listed as such.
- Resolved 2026-09-30: #6 corpus coverage, generic category, per-provider ≥ 99%, coverage metric, negatives licensing + pre-scan (D-0007 amendment 3, A-0010 reworded).
- Resolved 2026-09-30: D-0009 accepted (re-run Go comparison on every gitleaks version change);
  D-0007 amendments (engine; corpus via provider-format generators, independent of the rules; negatives
  committed); corpus option (c); D-0010 pre-commit secret scan.
- Resolved 2026-09-30: data-key shred finality via master-key rotation (D-0004 amendment 7).
- Resolved 2026-09-30: checkpointer role = new `nacre_checkpointer` (D-0005 amendment 2).
- Resolved 2026-09-30: shredding final at root rotation (D-0004 amendment 6; A-0008 test updated).
- Resolved 2026-09-30: D-0008 accepted (payload_type kept in AAD, flags byte, header in AAD, own
  codec: no floats, separate strict decoder, cbor2 + hypothesis test-only, frozen vectors).

## Local design notes from #3 (D1; recorded here and in file headers/comments)
- Roles are created NOLOGIN by migration 0001; login and passwords are set by ops (tests: fixture).
  Migrations run as the admin account from NACRE_DSN_MIGRATOR and `SET LOCAL ROLE nacre_migrator`,
  so nacre_migrator owns every object (tested).
- Settings: `nacre.read_streams`, `nacre.write_streams`, plus `nacre.principal` so a principal can
  read its own grants before any stream is readable (the D-0005 door will set it; A-0012 applies).
- `scope_grants` is insert-only; a row is a principal's full access to one stream as of an
  org-stream commit_seq; the highest source_seq wins; revoke = both flags false; append ⇒ read (CHECK).
- The linkage trigger takes the stream lock itself and runs as invoker, so append-without-read
  fails closed (tested).
- System subject = `subject_id = stream_id` in `keys.data_keys`.
- Enums are `text` + CHECK (not PG enum types); short identifiers limited to `[A-Za-z0-9._:/+-]{1,128}`.
- UPDATE/DELETE policies for scope status, key shredding and root-key rotation are deliberately
  absent; each arrives as a new migration with the file that needs it (#20, #20a, #21).
- **Finding for A-0008:** deleted wrapped-key bytes survive in dead tuples, WAL, replicas and backups.
  Resolved by the owner via D-0004 amendment 6 (finality at root rotation), not by VACUUM.

## Blockers
- None.

## Verification
- See the latest entry in "Session log" below.

## Session log
- 2026-09-30: planning accepted and committed (38886e1).
- 2026-09-30: INDEX #1 `core/event.py` + `tests/core/test_event.py`. INDEX paths switched to
  repo-relative because check_structure.py matches `src/nacre/...` (first run failed: "not registered").
  Results: `check_structure.py` → 0 failure(s), 0 warning(s); `pytest` → 16 passed.
  Also committed: owner's CLAUDE.md line "repo rules take priority over global ones always".
- 2026-09-30: owner confirmed key custody; D-0002/D-0004 amendments, D-0007 accepted, D-0008 proposed, A-0017 added.
- 2026-09-30: INDEX #2 `core/db.py` + `tests/core/test_db.py`; `docker-compose.yml`
  (postgres:17.11@sha256:d74eeac9…), `tests/conftest.py`. Installed psycopg 3.3.6, cryptography 50.0.1.
  First run: 1 failure, a bug in the test helper (unencoded DSN options, which `connect()` also
  overrides); rewritten to set the hostile default on a scratch role. Mutation check: removing the
  READ COMMITTED line makes that test fail, as intended.
  Results: `check_structure.py` → 0 failure(s), 0 warning(s); `pytest` → 23 passed.
- 2026-09-30: D-0008 accepted with owner resolutions; A-0017 test widened to google-re2; standing instructions recorded.
- 2026-09-30: INDEX #2a `core/blob_store.py` (Protocol only; write-once, no delete). Results: check_structure 0/0; pytest 23 passed. No test file: an interface is exercised through #15a.
- 2026-09-30: INDEX #2b `core/root_key_provider.py` (Protocol + WrappedKey; wrapping bound to stream_id context, D1). Results: check_structure 0/0; pytest 23 passed. Exercised through #11a.
- 2026-09-30: INDEX #3 `schema/apply_migrations.py` + `sql/0001_ledger.sql`, `0002_scopes_rls.sql`,
  `0003_keys.sql`, with tests (runner, append-only, linkage, envelope checks, D-0005 S-1, S-2, basic S-3,
  keys). #3d deferred (checkpointer role, see Open questions). All schema tests passed on first run, so
  they were mutation-checked: dropping the prev_hash check, opening the app read policy, removing the
  append-only trigger, and granting the app UPDATE each made the targeted test fail at its assertion.
  Results: `check_structure.py` → 0 failure(s), 0 warning(s); `pytest` → 82 passed (4.4 s).
- 2026-09-30: D-0005 amendment 2 (nacre_checkpointer, C-1..C-6), D-0004 amendment 6 (finality at root rotation) + open issue on data-key finality; A-0008 re-specified.
- 2026-09-30: INDEX #2c `core/encode_cbor.py`, #2d `core/decode_cbor.py`, #4 `ledger/encode_envelope.py`, #5 `ledger/seal_event.py`, #3d `sql/0004_checkpoints.sql`. Installed test-only cbor2 6.1.4, hypothesis 6.168.3
  (pyproject `[project.optional-dependencies] test`). Mutation checks: removing key sorting → 4 failures;
  shifting the 2-byte length boundary → caught only by hypothesis at first, so explicit shortest-form
  boundary vectors were added (now 2 deterministic failures). Our bytes match cbor2 canonical on 500
  hypothesis cases. D1: MAX_DEPTH = 64. Results: check_structure 0/0; pytest (encoder file) 76 passed.
- 2026-09-30: INDEX #2d `core/decode_cbor.py`, #4 `ledger/encode_envelope.py`, #5 `ledger/seal_event.py`, #3d `sql/0004_checkpoints.sql`. First draft had an unbounded `data[pos]` read in the
  map loop (would raise IndexError, not CborDecodeError, on input cut mid-map); fixed before testing,
  covered by the "map cut off mid-entry" vector and a 2000-case fuzz test. The final re-encode guard
  masks individual checks, so each check was mutation-tested with the guard disabled: key order
  (4 fail), shortest form (8 fail), trailing bytes (2 fail), depth limit (2 fail); guard-off alone:
  all pass (guard is redundant by design). Results: check_structure 0/0; pytest 258 passed.
- 2026-09-30: INDEX #4 `ledger/encode_envelope.py`, #5 `ledger/seal_event.py`, #3d `sql/0004_checkpoints.sql`. Tag bytes 0x00–0x05 and a uint16 version prefix
  are D1 details within D-0002 option b (in file header). AAD bytes checked against a hand-written
  layout; seal field set pinned to core.event.Envelope minus hash; seal encoding frozen by sha256
  (bd0978…a5ee, 442 bytes). Mutations: no length prefix → 3 fail; body dropped from seal → 6 fail.
  Results: check_structure 0/0; pytest (file) 19 passed.
- 2026-09-30: INDEX #5 `ledger/seal_event.py`, #3d `sql/0004_checkpoints.sql`. Frozen seal 7fb349…59be. Tamper tests (body, header,
  reorder, drop) fail recomputation; a test documents that a full rewrite with recomputed hashes is
  self-consistent, which is exactly what checkpoints (#18/#19) must catch. Mutation: prev_hash dropped
  from the formula → 2 fail. Results: check_structure 0/0; pytest (file) 15 passed.
- 2026-09-30: INDEX #3d `sql/0004_checkpoints.sql`: nacre_checkpointer role, column-granted head reads,
  append-only checkpoints, verifier read-only (C-1..C-6 tested). Public keys deliberately not stored in
  the DB (the verifier must trust only configured keys), noted in the migration. First run: 1 failure,
  a test bug (bytes passed for a uuid param). Mutation checks: full-row SELECT grant → 8 fail; no
  append-only trigger → 2 fail; verifier granted INSERT → SURVIVED (forced RLS still blocked the
  insert), so explicit has_table_privilege tests were added; the mutant now fails.
  Results: check_structure 0/0; pytest 333 passed.
- 2026-09-30: D-0004 amendment 7 (master rotation after data-key shreds), A-0008 extended, INDEX #20b added.
- 2026-09-30: A-0017 evaluation (scripts/a0017/, evidence/A-0017*). gitleaks v8.30.1, 221 regex rules,
  1,200-file corpus + 7,290 generated docs; Go regexp as ground truth. google-re2: 221/221 agree
  with Go, worst 0.018 s/20 KB. Python re: 22 compile failures, 1 misread, 10 disagreements (ASCII
  mode), 1 exponential rule. The first timing run was flawed (cumulative, not per-probe, timeout);
  it was fixed, rerun, and the flawed output kept. A-0017 marked invalidated; A-0018 and D-0009
  (proposed) added.
- 2026-09-30: D-0009 accepted, D-0007 amendments 1–2, D-0010 accepted, A-0010 test method updated.
- 2026-09-30: D-0010 pre-commit hook. Vendored gitleaks v8.30.1 rules + MIT licence
  (`src/nacre/ledger/data/`, sha256 matches the pin) and THIRD_PARTY_NOTICES.md; google-re2 1.1.20251105
  pinned in pyproject. Temporary scanner honours keywords, secretGroup, entropy, global and per-rule
  allowlists. Whole tracked tree: 2 findings, both reviewed and allowlisted with reasons: the vendored
  rules file itself, and the throwaway Docker test-DB role password in tests/conftest.py. The hook
  blocked a real commit attempt with a runtime-built GitHub-token probe. On the unredacted A-0017
  results it flags 6 strings where GitHub flagged 11: GitHub's detectors differ from gitleaks', which
  is recorded and not treated as equivalence. The hook then blocked this very commit: the new test
  file held the allowlisted test-password line as a literal; it is now built at runtime.
  Results: check_structure 0/0; pytest 340 passed (51 s; the machine's load average was ~10–14 from
  other work, which slowed the hypothesis tests).
- 2026-09-30: INDEX #9 `scopes/resolve_access.py`. Mutation: oldest-grant-wins → 1 fail. Build order changed: #7/#8 after #14 (dependency on append_event). Results: pytest (scopes) 7 passed.
- 2026-09-30: INDEX #10 `scopes/open_scoped_session.py`. Pre-flight refusals (D1): open transaction,
  superuser/BYPASSRLS role, isolation ≠ READ COMMITTED. Adversarial suite covers all 25 scope-kind pairs
  (events, master keys, data keys, scopes, cross-scope key insert), no-grant, read-only, connection
  reuse across principals, error rollback, revoke. Mutations: session-level (not LOCAL) setting →
  1 fail; superuser refusal removed → 1 fail. Results: check_structure 0/0; pytest (scopes) 42 passed.
- 2026-09-30: D-0007 amendment 3 (provider set, generic category, per-provider targets, coverage metric, negatives rules); A-0010 reworded.
- 2026-09-30: INDEX #11a `keys/local_file_root_key.py`; RootKeyProvider protocol gained create_version /
  destroy_version (D1, needed by #20a rotation). Wrap AAD binds root version + stream context; files 0600,
  loose permissions refused; destroy = zero + fsync + unlink (local disk only; other copies are the
  operator step). Mutations: version dropped from AAD → 1 fail; permission check removed → 1 fail.
  Results: pytest (keys) 22 passed.
- 2026-09-30: INDEX #11 `keys/get_or_create_key.py` (get_or_create_key + load_key; one functionality:
  data-key resolution). Wraps bound to key id/stream/subject/month; master wrap bound to stream; no cache (D1).
  Tests include 4-thread concurrent creation, shredded data/master key → None, moved wraps fail,
  destroyed root version → unresolvable. Mutations: ON CONFLICT removed → 2 fail; DEK wrap AAD reduced
  → 1 fail. Results: pytest (keys) 36 passed.
- 2026-09-30: INDEX #12 `keys/encrypt_payload.py` (+ derive_mac) and #13 `keys/decrypt_payload.py`.
  First test run HUNG (>5 min): a test bug, not a product bug. A fixture held an uncommitted session that
  had just inserted the stream master key, and a second session inserting the same key waited on it
  forever. Fixed by creating keys in their own committed session. Guard added: every test database now
  has lock_timeout = 10s, so a future lock wait fails loudly instead of hanging.
  Mutations: decrypt readability check removed → 1 fail (an unreadable stream would have been reported
  as Shredded); flags check removed → 2 fail; use count not incremented → 2 fail.
  Results: check_structure 0/0; pytest (keys) 71 passed.
- 2026-09-30: Provider-format research (agent; provider-owned sources only, no scanner rules) saved:
  evidence/A-0010-provider-formats.md (summary) + ...-full-unverified.md (81 rows, sanitized, unverified
  until each generator is written). The pre-commit hook flagged the appendix: gitleaks' private-key rule
  spans prose between quoted PEM labels. No key material was present. Added a narrow allowlist (matches
  containing a backtick or a pipe, in the format docs only); a test proves a real-shaped PEM in the same
  file is still caught. Finding for #6: the private-key rule redacts docs that merely quote PEM labels,
  so it is an FP source to measure.
- 2026-09-30: D-0007 amendment 4 (no-doc providers unmeasured + generic-layer measurement; owner-measured facts via local script, SDK check before fallback; public credentials neutral, not stripped, tagged; look-alike rules).
- 2026-09-30: `scripts/measure_token_format.py` for the owner. It never prints token-derived characters:
  the prefix shown is the one the owner typed, confirmed yes/no. Tests assert no 4-char substring of any
  token appears in the output, including prefix-less hex tokens. Mutation: reporting the token's first
  10 chars as the prefix → 4 fail.
- 2026-09-30: #6 corpus, part 1: framework (`secret_corpus/corpus.py`: registry, per-generator seeds,
  neutral-name contexts, "caught = secret string gone", pinned sha256) + generic category
  (`secret_corpus/generic.py`): PKCS#8 RSA/EC, PKCS#1 RSA, SEC1 EC, OpenSSH ed25519/RSA (hand-built
  per PROTOCOL.key for determinism), PostgreSQL/MySQL/MongoDB URIs, URL userinfo, HS256 JWTs, .env.
  D1 choices: .env dialect = Docker Compose ".env file syntax"; encrypted PEM/OpenSSH variants omitted
  (standard encryption draws random salts, which would break the pinned digest); RSA primes by seeded
  Miller-Rabin with 8 rounds and 6 keys per RSA format, bringing the build from 25 s to 10 s.
  Every output is validated by an independent parser (cryptography loads every key; urllib parses
  every URI). Mutation: broken OpenSSH padding → 2 fail. 468 samples, sha256 10be73…bcec.
  Results: pytest (corpus) 15 passed.
- 2026-09-30: #6 corpus, part 2: negatives.
  - Committed: 310 files (2.1 MB, each truncated at a line boundary to 16 KiB) from the 12 permissively
    licensed installed packages. hypothesis (MPL-2.0), psycopg (LGPL-3.0) and pip/_vendor are excluded.
    Each package's licence files sit beside its files; provenance and sha256 are in negatives/MANIFEST.json;
    the builder is tests/ledger/secret_corpus/build_negatives.py.
  - Also committed: lodash 4.17.21 minified (MIT), integrity matched against cdnjs SRI.
  - Pre-scan: 1 file flagged (cryptography hpke.pyi, generic-api-key on a `private_key: X25519PrivateKey`
    type annotation). Reviewed as a FALSE POSITIVE and kept, because FPs are what the ≤ 2% rate measures.
    No real secrets found; nothing excluded.
  - Synthetic negatives (generated): UUID lists, git logs, lockfile integrity / pip hashes, base64 blobs,
    content hashes.
  - The pre-commit scan skips committed negatives only while their bytes match the reviewed manifest
    sha256 (tested). THIRD_PARTY_NOTICES.md updated.
  - Corpus sha256 is now 6a64f6…7632. Mutation: editing one negative → the manifest test fails.
  - Note: 310 files, not the 1,200 of the A-0017 set, because only 12 packages qualify under the licence rule.
- 2026-09-30: #6 corpus, part 3: provider generators for the fully documented set (see Open questions).
  Each is checked against the provider's own rules: GitLab CRC recomputed as GitLab computes it, Supabase
  checksum recomputed, Sentry tokens parsed as Sentry parses them, PyPI tokens matched against PyPI's
  published pattern, GitHub ghs against GitHub's published regex. Public look-alikes (sb_publishable,
  anon JWT, public DSN) are generated as expected="public".
  First run: 1 test bug (split the Supabase checksum on '_', which base64url may contain; now parsed by
  position). Mutations: GitLab CRC without prefix → 1 fail; anon generator emitting service_role → 1 fail.
  Corpus sha256 686f67…b175. Results: pytest (corpus) 32 passed.
- 2026-09-30: D-0008 amendment 5 (optional public_credentials body key), D-0007 amendment 5.
- 2026-09-30: INDEX #6 `ledger/strip_secrets.py` built: gitleaks under RE2 (sha256-pinned; any compile
  failure fails the load), public layer, entropy layer. Measurement (tests/ledger/secret_corpus/measure.py):
  FP 2/561 = 0.36%, public kept 100%, 8 groups below target (see Open questions); coverage 12/221 rules.
  Tuning path, reported in full:
  - First run: FP 4.1%. A bug: the entropy candidate truncated long runs to 120 chars instead of
    rejecting them; fixed → FP 1.25%.
  - Then two D1 cue rules (skip `//` URL tails; after a spaced ` = ` only quoted values) → FP 0.36%.
  - The bug fix also removed accidental entropy catches of long ghs_/sntrys_ tokens. Correct: known
    formats need rules (D-0011).
  Other bugs: re2 has no IGNORECASE (inline (?i) used); a nonsense test line was removed before commit.
  Mutations: public layer off → 2 fail; rules pin off → 1 fail.
  Results: pytest (strip_secrets) 22 passed + 8 strict xfail.
- 2026-09-30: D-0011 accepted with owner conditions (holdout, additive-only, admission criteria, loosening = owner approval).
- 2026-09-30: SEALED HOLDOUT committed before any Nacre rule (D-0011 amendment 1). It uses its own seed
  (77031117) and 9 contexts absent from the working set (TOML, XML, Markdown code block, Go, JS, X-Api-Key
  header, SQL, CLI flag, error message); committed negatives are split by path hash (174 working / 137
  holdout); holdout digest pinned e13800…2345. Holdout measurement tests are added only after the rules
  are written, so no holdout result is seen while writing rules.
- 2026-09-30: D-0011 implemented: nacre-rules-v1.toml (5 rules, sha256-pinned, additive only: duplicate
  ids refused, gitleaks global allowlist not applied to Nacre rules). Working set: all 8 former gaps now
  100%. First HOLDOUT run (rules frozen first): Nacre-targeted kinds 100%; JWT / service_role / sntryu_ 78%
  (vendored-rule terminators; overfitting flag). Holdout FP 0.52%, working FP 0.24%. Admission fields
  (holdout results) filled in each rule; nacre pin updated. Evidence: A-0010-measurement-2026-09-30-holdout.md.
  Hook switched to strip_secrets. The whole-tree scan found 6 false positives, all reviewed and
  hook-allowlisted exact-value with reasons (bracket and f-string placeholders, dev-DB password, Base62
  alphabet). D1: merged spans are labelled by the most specific rule (provider > generic > entropy),
  which changes labels only. Fixed a test bug: the tree-clean test now applies the hook's negatives skip.
  Results: check_structure 0/0; pytest 531 passed + 3 strict xfail.
- 2026-09-30: D-0011 amendments 5-7 (re-seal protocol, boundary principle, loosening declined); holdout log started (H1 demoted).
- 2026-09-30: Boundary fix per D-0011 amendment 6, written after H2 was sealed (633f637), before any H2
  measurement. Reading the vendored rules: jwt and sentry-user-token end on a terminator list, and jwt's
  segment charsets contradict RFC 7515; gitlab-pat-routable does not match GitLab's current two-dot format,
  so routable tokens had been only partially redacted by gitlab-pat. Added 3 additive Nacre rules:
  jwt-compact, sentry-user-token-bounded, gitlab-pat-routable-current. Residue metric added (reported only).
  First H2 run: catch 100% in every group; FP 3.25% (measurement flaw, see Open questions); real-code FP 0/150.
  Evidence: A-0010-measurement-2026-09-30-H2.md. The suite now takes about 3.4 min (H2 builds 1,448 samples
  incl. RSA ×16 contexts several times); session caching is a later D1 improvement.
  Results: check_structure 0/0; pytest 536 passed + 1 strict xfail.
- 2026-09-30: D-0011 amendment 8 (benchmark method); H2 FP marked invalid by construction in the holdout log; upstream note extended.
- 2026-09-30: Benchmark-method framework (D-0011 amendment 8), opt-in so earlier corpora rebuild
  byte-identically (working, H1 and H2 digests verified unchanged):
  - credential-slot category (5 random-value kinds), placed only in labelled credential-slot contexts;
  - synthetic negatives never embedded;
  - per-character "caught" over per-generator secret parts (documented prefixes and PEM armor may
    remain), plus longest surviving fragment;
  - working data = working set + H1, with slots labelled; pinned 8132ea…d209.

  Slow `holdout` marker: default `pytest` deselects it (45 s). The pre-commit hook runs `pytest -m holdout`
  when detector/rules/corpus files are staged. RSA keys are memoised on rng state (the post-state is
  restored, so digests are unchanged) and persisted to a git-ignored cache: H2 rebuild goes from ~60 s
  cold to 5 s warm.
- 2026-09-30: #6 CLOSED. H3 (sealed 3700f1f, separate session) official results: every provider/generic
  group 100% per-character (longest surviving fragment 0), FP 0/400, public kept 100%; H2 recomputed
  per-character 100% for the record. Credential-slot category (ungated) 54–72% on H3 vs 82–83% working
  (flagged). No detector change after H3 was sealed. Evidence: A-0010-measurement-2026-09-30-H3.md.
  H2 FP xfail removed (H2 is record-only; FP invalid by construction per the holdout log).
- 2026-09-30: #14 started with a design read; two unrecorded choices found; D-0012 proposed (trust mapping D3, request MAC D2). No #14 code written.
- 2026-09-30: D-0012 accepted with corrections; D-0002 amendment 4 (envelope v2 with trust_basis); credential-slot target recorded as a Phase 3 gate item.
- 2026-09-30: Envelope v2 (D-0002 amendment 4): TrustBasis enum, Envelope.trust_basis, encode_envelope v2 lists (v1 frozen, unchanged), migration 0005 (fix-forward). New frozen v2 seal vector 4b7e2e…546a. Results: pytest 518 passed.
- 2026-09-30: INDEX #14 `ledger/append_event.py` (290 lines).
  Validation choices (D1):
  - occurred_at finer than its precision is rejected, not truncated;
  - idempotency keys must be canonical UUID v4/v7;
  - trust by source + authorship per the D-0012 table;
  - the subject is the person for person statements/messages, otherwise the stream (or explicit).

  Tests (37) cover:
  - round trip and seal re-verification;
  - secrets absent from every stored byte;
  - public-credential tagging;
  - the trust table;
  - exact retry → original, no row; conflict on different content or principal; OriginalErased;
  - canonical-UUID keys;
  - validation rejections write nothing;
  - person erasure shreds only that person's events;
  - read-only principal refused;
  - 16 concurrent writers → gapless valid chain.

  Mutations: content not stripped → 1 fail; principal dropped from the MAC → 1 fail; app-level lock
  removed → 1 fail. "Tool trusted" at first SURVIVED (a later clause also yields untrusted), so
  distinguishing cases were added (tool/web + integration_result claim) and it is now caught.
  Shared fixtures (provider/streams/session) moved to tests/conftest.py.
- 2026-09-30: A-0007 measured (gate item 6), UNPOOLED: 1 writer 107/s, p99 12.9 ms; 16 writers 234–239/s,
  p99 ≈ 80 ms (threads and processes agree, so the stream lock, not the GIL, is the limit). D1 change first:
  work that needs no sequence number moved before the stream lock (16-writer p99 111.5 → ≈ 80 ms; the
  first run is kept in the evidence).
- 2026-09-30: INDEX #7 register_scope, #7a bootstrap_org (new file; D-0005's "special bootstrap step"),
  #8 set_access, migration 0006 (composite same-org FKs).
  - Each writes its config event through append_event and its projection row in the same transaction,
    with source_event_id / source_seq pointing back to the event.
  - Retries are exact (caller-chosen stream id) and write no second row.
  - Only org-stream appenders can register or grant.

  D1 notes:
  - bootstrap_org is the one deliberate bypass of open_scoped_session (admin connection, SET LOCAL ROLE
    nacre_app, settings admitting only the new org).
  - Revoke = all-false set_access.

  Mutations: retry writes a row anyway → 1 fail; append-implies-read check removed → 1 fail (the DB CHECK
  raises a different error). Results: pytest (scopes) 55 passed.
- 2026-09-30: A-0007 re-measured against the rescoped target: ≤ 4 writers p99 12–21 ms (met), 16-writer stress p99 75–94 ms (under the 150 ms ceiling), threads and processes. A-0019 added. Bench tests behind the `bench` marker.
- 2026-09-30: #7a hardened per owner review. Confirmed and tested: admin-only (refuses the app, verifier and
  checkpointer roles), records its own ledger event (the org stream's first event). Added: refuses an org id
  that already names a scope or a stream, checked under the stream lock in the same transaction. Mutation:
  existence check removed → 2 fail.
- 2026-09-30: INDEX #16 read_stream (+ head) and #17 replay_cycle.
  D1 notes:
  - AS_OF beyond the head is REFUSED, not clamped (ADR-0010 rule 9: only exposed watermarks are stable);
  - an unreadable stream is refused explicitly, not returned empty;
  - Shredded events are returned as Shredded;
  - replay is single-stream (cross-stream needs Phase 3's watermark vector).

  Gate item 2 is tested (AS_OF(3) identical after 10 later commits). Mutations: beyond-head check removed →
  1 fail; ordered by time instead of commit_seq → 3 fail.
- 2026-09-30: D-0013 and D-0014 accepted with owner additions; D-0006 amendment 1 (psycopg_pool); D-0004 amendment 8 (weekly root rotation); A-0020.
- 2026-09-30: psycopg_pool integrated (core.db.open_pool); pooled-scope-leak test added (mutation: session-level setting → fails). A-0007 re-measured pooled: ≤ 4 writers p99 ≤ 24.0 ms, 16-writer stress ≤ 87.8 ms. A-0007 validated for Phase 1.
- 2026-09-30: INDEX #15 store_attachment, #15a local_disk_blob_store, #15b read_attachment (new); #15c orphan GC planned.
  Shared crypto factored into keys: seal_bytes / open_bytes / check_header, so bodies and attachments use one format.
  append_event takes attachments: stored before the lock (and so before commit); text-like attachments are stripped;
  metadata goes in the encrypted body.
  Tests: round trip; dedup within a key but not across streams; the file exists before commit, and an aborted
  append leaves only a harmless orphan; text stripping; flipped / truncated / swapped blobs caught; missing blob is
  an error; Shredded; limits.
  Mutations: sha check off → 3 fail. The plaintext-ref check off at first SURVIVED (AEAD already binds blob to ref),
  so a writer-bug test was added (right ref, wrong plaintext); it now fails.
  Note: append_event.py is 338 lines (soft limit 300, hard 400); split validation out if it grows further.
- 2026-09-30: INDEX #18 write_checkpoint and #19 verify_chain.
  - Signed message frozen, with signing_key_id carrying the key version.
  - The witness is the trust anchor: a DB checkpoint absent from the witness = tampering; a witness entry absent
    from the DB = warning (crash window).
  - Streams never checkpointed are checkpointed on the next round (the checkpointer cannot read event times).

  Tamper suite (gate 3): edited ciphertext, edited header, deleted row, reordered rows, and a full rewrite with
  recomputed seals (caught ONLY by the checkpoint); untrusted key and altered signature caught. A destroyed master
  key still verifies (gate 4, chain side). Mutations: checkpoint-on-chain → 1 fail; seal recompute → 2 fail;
  signature check → 1 fail.
- 2026-09-30: Owner fixes.
  - Text vs binary is decided by content (a relabelling test: text declared image/png or octet-stream is still
    stripped; mutation "decide by media type" → 2 fail).
  - Attachment `scan` marker (D-0008 amendment 6).
  - Validation split into ledger/validate_append.py (#14a): append_event.py 339 → 208 lines; request types
    re-exported.
  - Verifier: confirmed and tested that a rewrite is caught through the witness even after the DB checkpoint row
    is deleted; the warning wording no longer assumes a crash.
- 2026-09-30: Migration 0007, the nacre_keyadmin role (D-0014). D1: NOINHERIT member of nacre_app, so one transaction can destroy keys AND append the audit event (never half-done); least privilege holds per statement. Tested: owns nothing, no bypass, no inherit, never reads events/checkpoints, privilege table.
- 2026-09-30: D-0014 lifecycle built.
  - #20 manage_shred_requests: request / cancel / legal hold; 7-day grace; authority = org admin or the person
    themself; state derived from org-stream events.
  - #20c execute_due_shreds: one transaction per request destroys keys + writes deletion markers (in affected
    streams) + shred_executed (org stream).
  - #20d keyadmin_session.
  - #21 folded into these (delete_scope is a request kind).

  Tests (9): authority, grace, cancel, erase-only-that-person, self-erasure with hold and expiry, hold rules,
  delete_scope, forget_period, chain still verifies (gate 4 end to end), and execution runs once. Mutations: no
  grace → 2 fail; holds ignored → 1 fail; erase deletes all keys → 1 fail.
- 2026-09-30 — **#20b master rotation, #20a root rotation, A-0008 validated.**
  - `keys/rotate_master_key.py`:
    - streams needing rotation are derived from the org stream (a data-key shred_executed later than the last
      master_rotated, for streams that still have a master);
    - rotation is one transaction per stream: new master, rewrap surviving data keys, then master_rotated.
  - `keys/rotate_root_key.py`: refuses while master rotations are pending; needs the operator's non-empty
    confirmation that the separate backup was destroyed; rewraps in batches (resumable with `resume=True`);
    refuses to destroy if any master is still on an old version; writes root_rotated to every org stream
    BEFORE destroying (D1: a crash in between leaves recorded-but-undestroyed versions, never an unrecorded
    destruction).
  - `get_or_create_key.py`: public wrap_data_key / unwrap_data_key.
  - `local_file_root_key.destroy_version`: an absent version now raises KeyError (was FileNotFoundError).
  - Tests (10, `tests/keys/test_rotation_finality.py`): key rows are recovered from a real `pg_dump`; after
    erase, master rotation and root rotation, neither the old master nor the deleted data key unwraps; scope
    deletion is final; pending master rotations and a blank confirmation are refused; crashes mid master and
    mid root rotation, then resume; a leftover old-version row blocks destruction; an unreferenced current
    version is destroyed; the audit order is correct. Mutations: 9 run, 9 killed (4 survived the first pass;
    each got a test).
- 2026-09-30: #15c needs a D2 choice, so D-0015 is proposed and nothing is coded. BlobStore promises "no delete", and
  a dedup-versus-collector race could leave an event pointing to a missing blob. Recommended: a per-ref advisory lock
  (shared in append, exclusive try-lock in the collector) plus a 1 h minimum age, and a `nacre_gc` role. A-0022 is
  recorded as open.
- 2026-09-30 — **D-0015 accepted; #15c built; pooled key-admin reset tested; full Phase 1 gate run.**
  - Owner approved choices 1-4 (NOINHERIT keyadmin, audit-before-destroy, #21 folded into #20/#20c, the shared
    rotation test file).
  - `tests/keys/test_keyadmin_session.py` (new, 5 tests): one pooled physical connection returns to
    nacre_keyadmin with no nacre.* settings after:
    - a clean transaction;
    - an error raised in app mode, or after switching back to key-admin mode;
    - a database error.
    At the base role it cannot read events (NOINHERIT).
  - D-0015 accepted with owner amendments: lock before the existence check, held to commit; 24 h minimum age;
    contract "delete only through the cleanup, only for unreferenced blobs"; blobs of destroyed keys not deleted
    (a future D3 ADR, only after the finalising root rotation). A-0020 note: a cloud key service would make
    root-backup destruction verifiable.
  - Built:
    - migration 0008 (`nacre_gc`: SELECT(attachment_ref) only; two-integer lock-key functions, app and gc only);
    - BlobStore list_refs / delete / remove_stale_temp;
    - store_attachment shared lock;
    - `ledger/collect_orphan_blobs.py`;
    - DbRole KEYADMIN and GC.
  - Tests: 16 collector tests (`test_collect_orphan_blobs.py`) and 11 migration tests (`test_0008_orphan_collection.py`),
    including races in both orders and an event committed after the pre-filter. A-0022 validated.
  - Mutations:
    - collector, lock and keyadmin reset: 11 run. The first pass left 3 survivors; 2 got tests. The third (drop
      the pre-filter) is equivalent, and is documented in the file header.
    - Incident: a mutation run piped through `head` was killed before restoring `store_attachment.py`, leaving
      the lock removed. The two race tests caught it. Restored from the backup and diff-checked; no mutants remain.
  - Repo secret scan: gitleaks generic-api-key flagged the migration-name list in `test_apply_migrations.py`
    (`"0007_keyadmin_role.sql", "0008_...` on one line). Fixed by a line break, with no allowlist change.
  - **Phase 1 gate run** (Docker postgres 17.11):
    1. idempotency: 8 passed
    2. AS_OF: 2 passed
    3. tamper suite: 11 passed
    4. shredding + chain valid + finality: 30 passed
    5. cross-scope, door + real events: 52 passed
    6. A-0007 bench: 3 passed. POOLED script (the first version of this note said "unpooled", copied from a
       stale docstring; corrected), 4 writers: p99 22.7 ms (threads), 19.6 ms (processes);
       16-writer stress p99 93.3 ms; 0 errors; gapless.
    Full suite: 690 passed, 28 deselected (the holdout and bench markers); `-m holdout`: 25 passed; `-m bench`:
    3 passed; check_structure: 0 failures, 0 warnings.
- 2026-09-30 — **Phase 1 gate accepted (owner), with two follow-ups, both done:**
  - Gate item 6's official evidence = the pooled runs (e3206cf), confirmed by the pooled gate run. The earlier
    "unpooled" label in this file came from a stale test docstring; corrected in the file, the docstring and the
    evidence file.
  - Mutation runs never touch the working tree: `scripts/run_mutants.py` (temporary git worktree, baseline first,
    byte-for-byte tree check at the end), tested in `tests/scripts/test_run_mutants.py`. D1 note in the new
    `.claude/rules/testing.md`; CLAUDE.md now has a one-line pointer to it. Re-ran the D-0015 lock, collector and
    root-rotation mutants through it: 3/3 killed, tree unchanged.
  - Suite: 692 passed, 28 deselected; check_structure: 0 failures.
- 2026-10-01 — **Phase 2 planning (no code).**
  - **MNEXA read at `2fc460c`** (working tree dirty) by three read-only agents; the load-bearing claims were
    spot-checked by hand.
  - **Inventory:** `docs/plans/phase-2-mnexa-port-inventory.md`. Key findings:
    - consolidation 002–016 exists only in experiment scripts;
    - the experiments ran against substrate code not in git;
    - raw results were gitignored;
    - all runs are n = 1 on `gpt-4o-mini`, with no prompts recorded;
    - oracles were used in 006, 008 and 009;
    - there is no surprise or stakes gate, and `success` is never read;
    - episodes are ADR only;
    - the belief lifecycle is production code, deterministic only, with 4 bugs found by reading;
    - MNEXA's CURRENT.md is stale.
  - **Frozen before any port:** `tests/regression/mnexa/`, 8.3 MB, byte-identical (`cmp`), with `MANIFEST.json`.
    - Copied: task sets `tasks.json` and 003–016 with sidecars, the graders, and all 29 seed result files.
    - Hashes only (left in MNEXA): tasks 017–029, results 030–035, SQLite states.
    - The first attempt also copied 017–029 and 030–035. The repo secret scan flagged false positives: sha256 values
      under `*-api-*` keys, `msg_` response ids, and synthetic ids in `tasks_023`. Those files are now frozen by hash
      and the manifest was restructured. **No allowlist change.**
  - **Proposed ADRs:**
    - D-0016 regression suite, test modes and margins (D3);
    - D-0017 interpretation-plane schema;
    - D-0018 capture payloads and outcome authority;
    - D-0019 write-gate scoring;
    - D-0020 sleep-pass job;
    - D-0021 model-provider interface and policy;
    - D-0022 recording model calls.

    The D3 parts are marked. Every ADR lists its questions for the owner.
  - **Assumptions:** A-0023 … A-0028 (open).
  - **INDEX:** Phase 2 rows P1–P34 (planned); new folders `models`, `eval`.
- 2026-10-01 — **Owner framing applied; EXP-0001 prepared (not yet run).**
  - SPEC amended in three places: overview, lineage header and proof tier 1. MNEXA is prior art, not proof.
  - D-0016 accepted with owner amendments: fresh MNEXA baseline plus a no-memory control; 014–016 only; checker
    deferred; dated pins.
  - The D3 parts of D-0017, D-0018, D-0021 and D-0022 are approved. Their D2 parts and all of D-0019 and D-0020 stay
    proposed.
  - Assumptions: A-0023 superseded; A-0029 added (014–016 recorded **no** no-memory arm; checked in the results).
  - **Allowlist (owner-approved):**
    - recall task sets 017–029 and results 030–035 copied byte-identical (`cmp`);
    - 156 exact-value findings reviewed in `tests/regression/mnexa/SECRET_SCAN_REVIEWED.json` as path + rule +
      sha256 of the value, each with a reason. The classes are Anthropic `msg_` response ids and synthetic ids in
      `tasks_023`;
    - the scanner honours an entry only at its exact path, rule and value hash, and only under those two paths;
    - 2 new tests. One manifest false positive was removed by dropping a redundant field; no allowlist entry.
  - **EXP-0001:**
    - pre-registered in `docs/experiments/EXP-0001-mnexa-rebaseline.md`, committed before any run;
    - runner `scripts/run_mnexa_rebaseline.py`; environment `~/Desktop/nacre-runs/mnexa-venv` (Python 3.13.5,
      openai 3.22.1, sentence-transformers 6.1.0, torch 2.14.1; freeze saved beside it);
    - dry run complete without a key: 18/18 steps; 300 logged calls; the control has no memory; 0 passes on fake
      text; `.env` not copied; MNEXA tree unchanged;
    - real-mode logging wrapper tested offline against a stubbed SDK;
    - `summarize` reproduces MNEXA's historic 60/60/60 from the frozen results.
  - Verification:
    - check_structure: 0 failures.
    - pytest, first run right after the Postgres container started: **1 failed, 693 passed**. The failing test's
      name was not captured (only the summary line was kept).
    - Three later runs: 694 passed each.
    - Open task (identify this flaky test): **RESOLVED the same day**, see the next entry.
- 2026-10-01 — **Flaky test hunted and resolved (owner: before any Phase 2 code).**
  - **Hunt:** cold Postgres restart (`down` + `up`, tmpfs) before every iteration, then the full suite in random
    order (`pytest-randomly` 5.0.0, installed temporarily and uninstalled afterwards).
  - **Reproduced:** 2 failures in 11 iterations (seeds 255875928 and 1011632240), both
    `tests/scripts/test_run_mutants.py::test_uncommitted_changes_are_what_gets_mutated`:
    `assert 'SURVIVED' == 'killed'`. The earlier unnamed failure fits the same test, which was new in that session.
  - **Cause (a real bug in `scripts/run_mutants.py`, not a test bug):**
    - Python and pytest's assertion rewriter reuse a cached `.pyc` when the source's size and whole-second mtime
      match.
    - A same-size mutant (`== 2` → `== 3`) written in the same second as the baseline's compile ran the original code
      and was reported SURVIVED.
    - It can produce false survivors only, never false kills.
    - Reproduced deterministically outside the runner: the file says `== 3` and pytest passes.
  - **Fix:**
    - every inner pytest gets `PYTHONDONTWRITEBYTECODE=1`;
    - `_assert_no_bytecode` refuses to run if any `.pyc` exists in the worktree;
    - mutant writes go through `_write_source`.
  - **Deterministic test** `test_a_same_size_same_second_mutant_is_still_judged_on_its_own_code` forces the exact
    interleaving: same size and the same mtime in nanoseconds. It is proved by self-mutation: with the fix removed,
    the test fails 3/3; with only the env setting removed, the guard catches it.
  - **Two more runner bugs found while proving it:**
    - the baseline dropped flags but passed their values (`-k x`) to pytest as paths; it now runs each mutant's exact
      argument list (tested);
    - the runner hard-coded `ROOT/.venv/bin/python`, which does not exist inside a worktree; it now uses
      `sys.executable`.
  - **Impact on earlier mutation results:** none. Kills cannot be faked by stale bytecode, and every survivor
    reported so far came from a size-changing mutant.
  - **Confirmation:** a fresh 20-iteration run with a cold restart and random order each time, untouched tree:
    **20/20 clean, 697 passed each.**
- 2026-10-01 — **EXP-0001 complete and frozen; margins frozen; D-0019/D-0020 revised (proposed).**
  - Run `EXP-0001-20261001T071654Z-69b7b4`, verified independently:
    - 18/18 steps; 1,260 calls, all `gpt-4o-mini-2024-07-18`;
    - **B = 178/180, C = 21/180**; validity 87.2 pp; MNEXA safety all 0. A-0029 validated.
  - **Frozen** in `tests/regression/exp0001/`: run, summary, the full call log (the first recorded MNEXA prompts),
    9 harness results and 9 control results. Byte copies, scan clean. The manifest hashes all 1,275 files in the run
    folder. One entropy false positive on a manifest path was removed by storing paths as component lists; no
    allowlist change.
  - **Nacre's margins (frozen):** N ≥ 169 pooled, and per set 53 / 53 / 54; superiority 75 and 17 / 15 / 16 (implied;
    kept); zero safety tolerance.
  - **Shortcut audit** (`docs/plans/phase-2-shortcut-audit.md`):
    - MNEXA's harness injects task-author recoverable candidates, which contain the answer clause in all 60
      families;
    - attribution shows the model itself supplied the clause in all 180 B trials (147 through structured
      propositions, 33 through fallback with the model's same-run proposal);
    - recommendation: keep B as frozen, because the asymmetry runs against Nacre.
  - **D-0020 revision R1–R6 (proposed):** no injection; prompt parity against the recorded prompts; recorded
    formation parity; fallback mandatory; measured cost; transfer stays in the harness.
  - **D-0019 revision R1–R2 (proposed):** an authoritative correction always flags; flag recall is measured through
    the real capture mapping.
  - **No porting until the owner approves.**
- 2026-10-01 — **Owner: rebuild, not port.**
  - EXP-0002 cancelled. New bar pre-registered in `docs/experiments/EXP-0003-nacre-phase2-gate.md` before any Phase 2
    code: ≥ 162/180, ≥ 51/60 per set, ≥ 50 points over the same-run no-memory control, zero safety, no reruns.
    EXP-0001 is reference only.
  - SPEC (overview, lineage, proof and risks) and D-0016 amendment 2 updated.
  - D-0017, D-0018, D-0021 (amendment 1: pinned version, timeout/retry and token cost on every call) and D-0022
    accepted. D-0019 accepted with R1–R2. D-0020 accepted with R1 and R4–R6; R2–R3 dropped.
  - Kept L1 grader validation as the instrument (flagged to the owner).
  - **Interpretation flagged to the owner:** "keep gate items 13–15" conflicts with dropping R2/R3, since items 13
    and 14 *were* R2 and R3. Applied: drop 13–14, keep 15 (now item 11). Awaiting confirmation.
  - **Blocking question raised:** single-episode promotion (Open questions).
- 2026-10-01 — **Phase 2 build started. P1 and P2 done (instrument first).**
  - **P1 `eval/verify_frozen_suite.py`:** every copied file plus, when present, the 1,000+ hash-only originals (MNEXA
    states, the 031 workspace, the EXP-0001 run folder). All match. 5 tests.
  - **P2 `eval/grade_decision.py`:** port of `grade_text`, `semantic_grade` and `contains_any_regex`.
    - **L1:** reproduces all **2,070** verdicts MNEXA recorded on stored decisions of 003–016 (1,394 true,
      676 false), exactly. 9 tests.
    - **Mutations:** 9/9 killed. 5 survive L1 alone, because MNEXA's data never exercises phrase lists,
      `none_regex`, bare strings or forbidden hits; the contract tests guard them. Documented in the header.
- 2026-10-01 — **Models layer built: P6, P7, P9, P9b, P10** (D-0021 with amendment 1, D-0022).
  - **`core/model_provider.py`:** types plus the canonical request form (v1) and its hash; `is_dated_pin`.
  - **Price table `models/data/prices.json`, version 2026-10-01:** `gpt-4o-mini-2024-07-18` at $0.15 input, $0.075
    cached input and $0.60 output per 1M tokens, verified on OpenAI's model page the same day (listed as the default
    snapshot, no deprecation date). Fail closed for any other model.
  - **`models/set_model_policy.py` (P9b, new row):** org allow-list of dated pins; default deny; latest wins; an
    unreadable org stream means deny.
  - **`models/call_model.py`:**
    - **Checks before sending:** dated pin, price entry, provider policy, SI-1 (readable sources, one scope).
    - **Each attempt** becomes a `result` event (actor_kind model, untrusted, cycle_id = run) recording the canonical
      request and hash, the response, the timeout/retry settings, the attempt number, tokens, and the USD cost with
      the price-table version. A secret-shaped request or response is marked `redacted` and not replayable.
    - **Design fix found by a test:** provider failures are *returned* (`ModelCall.error`, `raise_for_error()`), not
      raised. Raising inside the caller's transaction rolled back the failed-attempt recordings.
  - **`models/recorded_provider.py`:** exact replay by request hash in recorded order; a miss raises
    `RecordingMiss`; no live fallback.
  - **`models/openai_responses_provider.py`:** SDK retries off; per-call timeout; errors classified retryable or
    not; error messages carry class and HTTP status only. Optional extra `nacre[openai]` = `openai==3.22.1`.
  - **`check_structure.py` rule 6 (SI-4):** each SDK may be imported only by its adapter.
  - **Tests:** 30 (`tests/models/`), including SI-1, SI-2 (canary key through the real SDK against a closed
    localhost port), SI-3, SI-5, SI-6 (sockets blocked in replay) and SI-7 (shredded scope means nothing
    replayable). **Mutations:** 12/12 killed on `call_model`'s guards.
  - **Not built yet (tracked):**
    - P8 Anthropic adapter (needs a dated pin and a price entry; not on the Phase 2 path);
    - P11 fixture loader;
    - the D-0022 attachment route for model-call bodies over 1 MiB (refused for now; Phase 2 bodies are about 4 KB).
  - **Correction to commit 4b01a54:**
    - **What happened:** its full-suite run had **1 failure**, and the commit chain still committed and pushed,
      because the exit status came from `tail`, not pytest.
    - **The failure:** test files imported helpers with `from conftest import …`. In a full run, `conftest`
      resolved to another folder's conftest (`tests/scopes/conftest.py`), so `transient` was missing. It passed in
      isolation. A test bug.
    - **Fix:** the helpers moved to the uniquely named `tests/models/model_fakes.py`; no other test file uses the
      pattern.
    - **Process fix:** suite results are now taken from pytest's own exit code (`pipefail`).
    - Full suite: **741 passed, exit 0**.
- 2026-10-01 — **Owner answers:**
  - **Single-source promotion:** option (a), with safeguards → D-0017 amendment 1.
  - **Gate:** mimicry items dropped; the three anti-shortcut guards are separate gate items 11–13. Final gate list
    above.
  - **Grader check:** kept as instrument validation.
  - **Next:** P12, P13–P21, P3.
- 2026-10-01 — **P12 done: migration 0009 `interp` projection** (D-0017).
  - **Structure only:** versions and edges, with no content columns (a test checks the column list).
  - **Integrity:** contiguous versions with fixed kind and stream; each version backed by a `memory_event` of the same
    stream at the recorded commit_seq; edges point only to earlier commits of the same stream (MNEXA ADR-0005 L-7);
    kind, status and support checks; append-only for the app (no grant) and the owner (trigger).
  - **Access:** forced RLS by stream; `heads` view with `security_invoker`.
  - **No foreign key to `ledger.events`:** it changed how the ledger refuses TRUNCATE, breaking 2 Phase 1 tests. The
    backing check is done by trigger instead.
  - **Tests:** 15. **Mutations:** 10/10 killed after 2 survivors got tests (version gap, FORCE RLS).
- 2026-10-01 — **P13–P19 done: capture** (D-0018).
  - **Recorders:** `record_decision` (decided_from optional in Phase 2), `record_prediction` (expected_success,
    confidence as an integer %), `record_action` (always dispatched), `record_outcome` (typed sections with closed
    roles; absence is never recorded as failure), `record_correction`.
  - **References (`validate_refs`):** the MNEXA ADR-0008 vocabulary, target types per relation, same stream,
    committed and readable, no duplicates; causal relation names are absent by construction.
  - **Authority (`section_authority`):** the D-0018 rule from the envelope only. It never reads text.
  - **Stakes tags:** a closed set (D-0019).
  - **Tests:** 45. **Mutations:** 10/10 killed on the authority rule and the reference validator.
- 2026-10-01 — **P20–P21 done: write gate** (D-0019 with R1).
  - **`score_event`:** surprise, stakes and statement in per-mille integers. R1: an authoritative correction or
    failing evaluation always scores 1000. Predicted failure that failed scores 0; predicted failure that succeeded
    scores 1000. Stakes tags on the outcome or its decision/action count. A trusted person statement or correction
    counts.
  - **`flag_events`:** mode thresholds `gate-thresholds-v1` (normal 500, incident 0 = everything). One `flag` memory
    event per target and version, with caused_by = target. Serialised by the stream lock.
  - **Tests:** 18, including 4 concurrent flaggers producing exactly 1 flag. **Mutations:** 11/11 killed after 2
    survivors got tests (statement needs trusted + person).
  - **Not built (tracked):** the D-0019 config keyword → stakes rules (tags only for now). The A-0028 100%-recall
    check on 003–016 runs once P3 exists.
- 2026-10-01 — **P3 done; A-0028 validated; anti-shortcut guard (i) in place.**
  - **`eval/load_mnexa_family.py`** maps the three frozen formats to capture events:
    - 003/004: live first decision plus feedback, with `success` from the ported grader;
    - 005–007: forced decision plus correction;
    - 008–016: role regions through the pre-registered EXP-0003 table, markers removed, no text outside regions.

    The outcome is a trusted `review` integration result; only its correction (or failing evaluation) section is
    authoritative.
  - **A-0028 validated (gate item 7):** the gate flags 100% of lesson-bearing episodes in every set 003–016.
  - **Guard (i), gate item 11:** a test asserts that no product module outside `eval/` references task fields,
    graders, role markers or the harness. Guards (ii) and (iii) come with the sleep pass.
  - **Requested scope done:** P12, P13–P21 and P3. **Next:** stores (P27–P34, including D-0017 amendment 1 tests),
    then the sleep pass (P22–P26), P11, P5.
- 2026-10-01 — **Gate item 7 selectivity pre-registered (D-0019 R3), before measuring.**
  - Frozen routine set: sha256 `cad974a7…`, 200 episodes. Ceiling ≤ 5%. Canary: flag-everything fails.
  - **Disclosed before measuring:** 32/200 are trusted expected failures that R1's failing-evaluation clause flags,
    so a failure (about 16%) is expected and will be reported, not engineered away.
  - **Note:** a first draft of the generator put expected failures in `diagnostic` sections. That would have dodged
    R1, so it was changed to `evaluation` (realistic) before freezing.
- 2026-10-01 — **Gate item 7 selectivity MEASURED: FAIL (32/200 = 16% > 5%).**
  - The failures are all trusted expected failures, flagged by R1's failing-evaluation clause. Recall still 100%.
    The canary (flag everything) fails item 7 as required.
  - **Item 7 is OPEN**, held by a strict xfail. Proposed R4 (owner decision): corrections always flag; failing
    evaluations flag only when unpredicted. Predicted: 0/200 routine, recall unchanged.
  - **Belief-store trigger confirmation (owner request):** `nacre_app` cannot disable or bypass the projection
    triggers. `DISABLE TRIGGER` (one / ALL / edges), `session_replication_role = replica` and `DROP TRIGGER` are all
    refused. A version cannot reference a missing event, an event of another stream, the wrong commit_seq, or a
    non-memory event. 22 tests in `test_0009_interpretation_plane.py`.
- 2026-10-01 — **P27–P34 done: stores** (D-0017 with amendment 1).
  - **`write_version` (P27b, new row):** the only writer of `interp`. Version event plus rows atomically; keyed MACs
    (new `MacPurpose.INTERP_MAC` under the event's own data key; no plaintext and no plain hashes in the projection).
  - **`propose_lesson`:** exact-span grounding, real decision → outcome ancestry, nucleus and qualifiers inside the
    span; `grounded_in_trusted_correction` = correction role + D-0018 authority.
  - **`promote_if_supported`:** quorum 2; single-source only for trusted, correction-grounded spans (50/50, versus
    quorum 80/100); a second agreeing episode upgrades; deviations 1–2; fallback records from unresolved proposals
    under the same evidence rule.
  - **`propose_contradiction` / `contest_belief`:** pinned to the exact head version; quorum 2; deviation 3
    (no-op unless active).
  - **`supersede_belief`:** contested old, active and different replacement, ≥ 2 shared decisions.
  - **`read_heads`:** as-of N; contested and superseded withheld with no fallback to older versions; shredded content
    is None.
  - **`rebuild_projection`:** recompute and compare (see Open questions).
  - **`commit_episode`:** MNEXA ADR-0009 Q-1…Q-15 (Q-9 partial, see Open questions).
  - **Tests:** 47, including one per amendment-1 safeguard and every Q-n.
  - **Mutations:** 17/17 killed after 2 tests were added (deviation 3 against a contested head; contested replacement
    refused). The dead `upgrade` condition was removed (an equivalent mutant: an upgrade always brings a new decision).
- 2026-10-01 — **P22–P26 done: sleep pass** (D-0020 with R1, R4–R6; rebuild, not port).
  - **`build_evidence_bundle`:** decision labelled as history; sections with envelope-computed authority.
  - **`admit_propositions`:** deterministic admission (no model call):
    - exact, unique quote in an AUTHORITATIVE section, else a hard reject with a reason (never a fallback);
    - duplicate spans rejected;
    - structure check (nucleus and typed qualifiers inside the quote) → support-first fallback when broken, unless
      already covered;
    - closed world.
  - **Seats:** `propose_propositions` and `repair_structure` use Nacre's own prompts, pinned by sha256 in tests
    (proposer and repair), strict JSON schema, temperature 0.0, model `gpt-4o-mini-2024-07-18`. A parse or provider
    failure yields no proposals, and the raw response stays recorded.
  - **`run_sleep_pass`:** start marker plus flags; per episode, each model call committed alone, then one atomic
    transaction (admission → proposals → promotion → episode → done marker); completion marker.
  - **Resume:** an existing recording of the same request is replayed, never paid twice.
  - **Not built (tracked):** belief-version corrections → contradiction proposals.
  - **Tests:** 27, including guard (iii) end to end (an untrusted outcome teaches nothing), crash mid-episode then
    resume with 0 live calls, and repair-vs-proposer selection.
  - **Mutations:** 13/13 killed (one test had a misquote that did not change the quote for some families; fixed).
- 2026-10-01 — **P11 and P5 done: the EXP-0003 harness is ready for the owner's live run.**
  - **`models/load_model_call_fixtures.py`:** export a stream's replayable calls as JSONL; load fixtures as
    `result` events marked `loaded_from_fixture`.
  - **`eval/run_phase2_gate.py`:**
    - **Transfer instrument:** MNEXA's fidelity-reasoner prompt verbatim (sha256 `b0a2b76d…` pinned),
      provider-default decoding, max_tokens 1024 (Nacre needs a cap; MNEXA had none; mean answer 26 tokens).
    - **Arms:** Nacre memory = recall-eligible heads (active beliefs + fallbacks), one segment each. No-memory arm in
      the same run. 016 uses majority of 3 attempts.
    - **Safety:** all six metrics, with the challenge check run separately and discarded (guard ii).
  - **`scripts/run_exp0003.py`:** live, dry and recorded modes; fresh database per run; frozen-suite check first;
    signal → aborted; summary applies the pre-registered bar, with EXP-0001 alongside (not a gate).
  - **Verified without a key:** dry run 180 trials with safety 0; recorded replay of it identical with 0 live calls.
  - **Tests:** 11, including gate 4 (recorded replay identical, network blocked), guard (ii), metric 5 positive
    case, the cross-scope probe both ways, and majority. **Mutations:** 7/7 killed after 3 tests were added.
  - **Open, needs the owner:**
    - the live EXP-0003 run;
    - gate item 7 selectivity R4 decision (item 7 currently FAILS: 32/200);
    - D-0017 rebuild wording;
    - ADR-0009 Q-9 epoch half.
- 2026-10-01 — **Owner:** R4 approved with anti-gaming rules (D-0019 R4, plus D-0018 amendment 1 for named checks);
  rebuild into generations (D-0017 amendment 2); the epoch half of Q-9 becomes a Phase 5 requirement.
  **EXP-0003 waits** until R4 is applied and the full suite passes.
- 2026-10-01 — **R4 applied and confirmed; D-0017 generations built; EXP-0003 unblocked.**
  - **D-0018 amendment 1:** `prediction.expected_failing_check` and `outcome.failing_checks` (validated).
  - **`gate/score_event`:** an authoritative correction always flags. A failure is unflagged only by a VALID
    prediction (linked, same decision, expected failure, names the check, committed before the first action or the
    outcome, failing_checks exactly that check). Vague, late, mismatched or extra failures flag. An outcome "for" an
    action resolves to that action's decision.
  - **Confirmation set v2:**
    - built by a SEPARATE agent session blind to the gate code, then verified structurally by this session;
    - **frozen in b6590c3 before measuring** (sha256 `b1900a51…`): 200 routine + 25 vague + 25 mismatch + 25 late.
    - The blind session reported changing two phrases after its own checks; this session re-ran every check on the
      final file (0 problems).
    - **Measured: 0/200 routine flagged, 0/75 adversarial missed.** Recall recomputed under R4: 100%. **Item 7
      PASSES.** Canary on v2 fails item 7, as required.
  - **v1 under R4:** 50/200 flagged (all its expected failures; it names no checks, so they are vague by
    construction). Historical only. Under R1 it was 32.
  - **Gate tests:** 12 new R4 cases (valid, case/space-insensitive, outcome-for-action, vague, mismatch, extra
    failure, unnamed outcome, late, correction overrides, other decision, raw prediction expecting success).
    **Mutations:** 8/8 killed after one defence-in-depth test was added.
  - **D-0017 amendment 2 (migration 0010):** generations per stream; readers and writers use the active generation.
    `rebuild_projection` recomputes in memory, and on a mismatch writes g+1 plus a switch record in one transaction,
    keeping g. Shredded rows are carried over. Aborts leave nothing. 6 new tests.
    **Mutations:** 7/8 killed; the "next generation" switch rule is an equivalent mutant (documented).
  - **Phase 5 requirement recorded:** no learning from evaluation epochs.
- 2026-10-01 — **EXP-0003 live run 8f32eb ABORTED by SIGHUP in rep 3 → excluded.**
  - Grades from reps 1–2 were not computed or looked at; only `run.json` was read (status, reason, progress).
  - **Runner fix:**
    - an ignored SIGHUP (nohup) stays ignored; SIGINT/SIGTERM (and SIGHUP when not under nohup) abort and are
      recorded;
    - per-family progress lines with `flush=True`;
    - run folder printed at start.
  - The same SIGHUP fix in `run_mnexa_rebaseline.py`.
  - **New tests** (`tests/scripts/test_run_exp0003_signals.py`, 4): under real nohup, SIGHUP is ignored and progress
    continues; SIGTERM still aborts and is recorded; without nohup, SIGHUP aborts and is recorded; SIGINT aborts; lines
    appear one at a time (not buffered bursts).
  - Mutations 3/3 killed (a first flush test did not discriminate; replaced). 5/5 repeat runs stable. No stray
    databases left by the killed test runs.
- 2026-10-01 — **EXP-0003 PASSED; record frozen; gate item 4 verified; remaining gate items run.**
  - **Run c07ad2 (code 73c73fe, clean):** Nacre 180/180, control 19/180, bar PASS, safety 0/0/0/0/0/0.
    Frozen byte-for-byte in `tests/regression/exp0003/` (183 files, manifest; secret scan clean). The frozen-suite
    checker covers it (gate item 1: 270 repo + 2,741 external files OK).
  - **Gate item 4 PASS:** an offline recorded replay of the frozen copy reproduced all 180 trials exactly, with 0 live
    calls and 0 network attempts.
  - **Ceiling audit:** 0 problems in 180 recorded families. Memory = the verbatim authoritative correction; the
    control is clean; no answer text outside authoritative correction spans; guards (i)–(iii) 6/6 on 73c73fe.
  - **Suite saturated** (Future-phase requirements).
  - **Gate item 9 OPEN:** person erasure does not erase derived copies (proved by a test, then removed: after
    deleting the person's data key, the statement was unreadable but the model-call record still held the words).
    D3 options under Open questions.
  - **Cost:** ≈ $0.00027 per flagged episode; ≈ $0.00037 per task.
- 2026-10-01 — **Gate item 8 PASSED on real state; D-0023 proposed for gate item 9.**
  - **Runner flags:** `--keep-databases` and `--rebuild-check`.
  - **Evidence:** an offline recorded replay of the frozen EXP-0003 record (network refused) rebuilt every scope from
    its ledger: **180/180 identical** (360 versions, 720 edges, 0 differences, 0 unverifiable). Grades identical to
    the live run again. Frozen in `tests/regression/exp0003_item8/` and covered by the frozen-suite checker.
  - **Kept databases (inspection):** `nacre_exp0003_460d69d265`, `_72924601c4`, `_c6b3273201`, with root keys in
    temp folders listed in the replay's run.json. Drop when no longer needed.
  - **D-0023 (D3, proposed):** contributor-set keys per (stream, set of contributing (subject, source month), month),
    with erase_person and forget_period reaching every derived record; mixed records erased whole; lost beliefs not
    recall-eligible; rebuild treats them as shredded; Phase 3 embeddings bound by the same rule.
  - **Second gap found:** D-0004's intake gives the person subject only to statements and messages, so a person's
    corrections are not erasable. D-0023 §6 proposes every person-actor event be person-subject (owner question 1).
  - **New assumptions:** A-0030 (contributor sets stay small), A-0031 (every derived record can name its sources).
  - Nothing built for item 9.
- 2026-10-01 — **D-0023 accepted (owner decisions 1–4) and BUILT; gate item 9 closed.**
  - **Housekeeping:**
    - dropped the 3 kept replay databases and their key folders;
    - also dropped 2 EMPTY `nacre_exp0003_*` databases leaked by my signal tests: the runner created its database
      before the cleanup `try`. Fixed (create inside it) and tested (killed runs leave no database).
  - **Migration 0011 `keys.key_contributors`:** (subject, source month, is_person) per derived key; cascade; RLS;
    keyadmin delete. The system subject is a member too (non-person), so forgetting a month reaches derived system
    content.
  - **`keys/derive_contributor_key.py`:** contributors from sources, recursive through derived keys; a shredded or
    foreign source → refused; cap 256 → `ContributorCapExceeded`. No fallback.
  - **`append_event` intake (supersedes D-0004's sentence):**
    - every person-authored event is under the person's key;
    - `on_behalf_of` puts an agent's event under the person's key;
    - `sources` → contributor-set key;
    - the MAC input changes only when these fields are used.
  - **Every derived writer passes sources:** model calls (cap and shredded-source check BEFORE the provider call);
    proposals; contradictions; versions (edges + carried prior version); flags; episode markers; fixtures (sources
    required).
  - **Sleep pass:** D-0023 refusals are recorded as content-free `derived_write_refused` markers and not retried.
  - **Erasure:** erase_person destroys every derived key with the person as a member (org-wide); forget_period
    destroys every derived key in the stream with a member month in the forgotten months.
  - **Recall:** `read_heads` excludes heads whose content is unreadable (lost beliefs); `include_unreadable` exists
    for structure views.
  - **Tests (D-0023 list):** `tests/keys/test_contributor_keys.py` (17) + a sleep refusal test:
    - erasure reaches derived records incl. derived-of-derived, and only those;
    - mixed records erased whole, either way;
    - a belief from an erased person's correction is lost; a legal hold keeps it until expiry; rebuild treats it as
      shredded;
    - forget-month via membership;
    - every person-authored event type, plus on_behalf_of;
    - rotation; membership RLS;
    - cap fail-closed (no key created, no provider call);
    - shredded sources refused;
    - structure test: no derived append without sources.
    - Phase 3 embeddings: the binding requirement is recorded in D-0023 §3 and the Phase 3 gate.
  - **Mutations:** 11/12 killed; `carried_from` is an equivalent mutant today (documented).
- 2026-10-01 — **FULL PHASE 2 GATE RUN on 8601ee6 (after D-0023): every item passes.**

  | # | Item | Result |
  |---|---|---|
  | 1 | Frozen-suite integrity | pass (now incl. the EXP-0003 record and its replays) |
  | 2 | Grader check | pass (2,070 verdicts) |
  | 3 | EXP-0003 bar | pass: live 180/180 vs 19/180 (run c07ad2 on 73c73fe); the offline replay on 8601ee6 reproduces it exactly |
  | 4 | Recorded determinism | pass on 8601ee6: 180/180 identical, 0 live calls, network refused, every request hash matched |
  | 5 | Belief lifecycle | pass |
  | 6 | Episode invariants Q-1…Q-15 | pass |
  | 7 | Write gate | pass: recall 100%, v2 0/200 routine, 75/75 adversarial |
  | 8 | Rebuild | pass on real state, 180/180 scopes identical on 8601ee6 |
  | 9 | Shredding and privacy | pass: D-0023 tests + SI-1…SI-7 |
  | 10 | Model-call records and cost | pass |
  | 11–13 | Anti-shortcut guards | pass |
  | 14 | Phase 1 gate | pass: 980 main, 25 holdout, 3 bench, check_structure 0 |

  - Evidence frozen in `tests/regression/exp0003_replay_8601ee6/`.
  - Request hashes all matched in replay, so D-0023 changed no model prompt: a new live EXP-0003 run would only
    resample the model. Optional; owner decision.
  - **Not gate items, still not built:** D-0019 stakes keyword rules; D-0020 corrections → contradictions; the
    D-0022 attachment route for model-call records over 1 MiB; the Anthropic adapter. **Open question:** events
    ABOUT a person written by others.
- 2026-10-01 — **Phase 2 gate ACCEPTED by the owner; tagged `phase-2-complete`** (at 8131c5d). No fresh live run is
  needed (prompts were verified identical by hash). **Phase 3 planning started (no product code).**
- 2026-10-01 — **Phase 3 planning delivered (no product code).**
  - **Embeddings measured** (research scripts in a separate venv; Docker Desktop; single connection, labelled):
    - warm exact top-50 search: 0.1 ms at 10k, 2 ms at 100k;
    - cold fetch + decrypt: 0.12–0.15 s at 10k, 1.2–1.5 s at 100k;
    - MiniLM query encode: 3.4 ms.
  - **Proposed:**
    - D-0024: encrypted recall index under each version's contributor-set key; a decrypted per-process cache with a
      `shred_epoch` erasure check; no pgvector;
    - D-0025: recall pipeline; snapshot = vector of per-stream positions; RRF fusion; quorum pruning; coverage;
      frozen CBOR frame; split ContextAssembled trace; latency targets;
    - D-0026: per-principal tokens, reject over-claims, `verified` trust_basis, MCP and SDK;
    - D-0027: binary scanning, reject on a secret, reject unscannable by default;
    - D-0028: Anthropic adapter.
  - **Owner question, pinning:** current Claude IDs are undated except `claude-haiku-4-5-20251001`.
  - **EXP-0004 draft:** arms C / V / N, k = 3, 1,080 trials per arm, bar N ≥ 0.80, N − V ≥ 15 pp, N − C ≥ 40 pp,
    safety 0.
  - **Phase 3 gate:** 16 items, including H4 (credential-slot target, ≥ 90% proposed) and binary scanning.
- 2026-10-01 — **Phase 3 plan APPROVED with changes.**
  - **Accepted:** D-0024 … D-0028, with the owner's decisions recorded in each.
  - **D-0024:** the cache enforces scope itself (grants confirmed in the snapshot transaction; every-pair suite through
    the cache; revocation and erasure invalidate by the next recall); onnxruntime + tokenizers.
  - **D-0026:** delegations are explicit, recorded, time-limited, revocable and scoped.
  - **D-0027:** no opt-out.
  - **D-0028:** Haiku dated snapshot only.
  - **EXP-0004 PRE-REGISTERED** with all numbers fixed: per-category floors ≥ 0.70 and ≥ naive; stale ≤ 0.05 of T3;
    budget 10 items / 4,000 chars; cap $15.
  - **H4 target:** ≥ 90% credential-slot catch, FP ≤ 2%.
- 2026-10-01 — **Sets frozen in separate blind sessions, each verified by this session.**
  - **EXP-0004:** test `93e1b689…`, dev `0e4874d9…`; byte-identical regeneration; checker 0 violations; no test text
    viewed.
  - **H4:** built (total `1cdc0d45…`; structure test 6/6) but **NOT COMMITTED.**
    - The pre-commit hook scanned its stdlib negatives, because `negatives_holdout4/` was not yet in the hook's
      manifest-skip table (my omission).
    - The scan **revealed one detector result to this session:** an entropy-layer flag on one negative file.
    - **Owner decision needed:** keep H4 sealed with the disclosure, or rebuild H4's negatives in a fresh session.
      Also: approve adding `negatives_holdout4/` to the hook's skip table (the H2/H3 mechanism).
    - The H4 files and the A-0010 log row stay uncommitted in the working tree.
  - **I1:** total `567b5931…`; 500 images regenerate byte-identically; evidence in A-0042.
  - **Open for the owner:**
    - the **D-0026 erratum** (ceiling table uses nonexistent sources; trust is source × authorship). This blocks R3;
    - I1 measurement questions (wrapped secrets; logical vs physical px; Pillow 12.3.0 as a test-only dependency);
    - H4 builder notes (slot 4 is a CLI flag; 3-line disjointness window).
  - **Next:** recall code (R6–R20) may start; the interface (R1–R4) waits for the erratum.

- 2026-10-01 — **Owner decisions applied:**
  - **H4:**
    - option (b), redraw only the stdlib negatives;
    - `negatives_holdout4/` added to the hook skip table;
    - **the hook fails closed** on unregistered secret-corpus folders (4 new tests);
    - disclosure logged in the holdout log;
    - **redraw BLOCKED:** only 128 candidates remain. H4 stays uncommitted pending an owner decision.
  - **D-0026 amendment 1 (D3):**
    - agents' structured events are verified but never authoritative; they count only under the two-decision rule;
    - agent free text is untrusted;
    - authoritative corrections come only from reviewer-granted persons or structured CI / integration results;
    - the claim table is encoded, with tests listed for R3.
  - **D-0027 amendment 1:** I1 success = "the secret is not stored"; wrapped secrets count; physical px (463
    gated).
  - **Pillow 12.3.0 test-only** (D-0006 amendment 2); 500 I1 images committed as the truth.
  - **Next:** recall code, R6 onward.
- 2026-10-01 — **R6 built:** migration `0012_recall_index.sql` and the `shred_epochs` bump in `execute_due_shreds`.
  - **Contents:**
    - `recall.index_generations`, with one embedder per generation;
    - `index_switches`: forward by one only;
    - `index_entries`: ciphertext plus ids only; the trigger requires a memory event of the same stream under the
      same key, and the generation's embedder;
    - all append-only, with stream RLS;
    - `keys.shred_epochs`: per stream, keyadmin-only.
  - **D1 refinements of D-0024:** keyed by stream (= scope, D-0005); the epoch is per stream, not per org, so it
    stays inside RLS.
  - **Renumbering:** the auth migration becomes `0013`.
- 2026-10-01 — **R6 check:** person erasure bumps the epoch in every stream where the person's keys existed: own keys
  in 2 streams, plus derived-key membership in a 3rd; the untouched 4th stays at 0 (new test).
- 2026-10-01 — **R7 and R8 built (embedder).**
  - **Dependencies:** numpy 2.5.3, onnxruntime 1.30.0, tokenizers 0.23.2 (exact pins, D-0024).
  - **Model:** all-MiniLM-L6-v2 ONNX at commit `1110a243` (Apache-2.0; `THIRD_PARTY_NOTICES.md`).
    - Not in git: `scripts/fetch_embedder.py` fetches from the pinned commit, or from our backup GitHub release
      `embedder-minilm-l6-v2-1110a243`; both sources verified to serve the pinned sha256s.
  - **Equivalence** vs frozen sentence-transformers vectors: min cosine **0.99999988** (bar 0.9999, 25 texts).
  - **Speed:** 1.4 ms per query on CPU.
  - **Structure rule:** only `embed_local.py` may import onnxruntime or tokenizers.
  - **Setup on a new machine:** `.venv/bin/python scripts/fetch_embedder.py` before pytest.
- 2026-10-01 — **H4 committed** with redrawn negatives, built by a separate blind session.
  - **Composition:** 128 stdlib + 22 permissive third-party files.
  - **Real-secret pre-scan:** detect-secrets; 0 real credentials found.
  - **Total sha256:** `4b573eef…`; composition logged in the holdout log.
  - **Not yet measured:** see the measurement-order question to the owner.
- 2026-10-02 — **Owner:** no H4 baseline; the credential-slot rules are written against H3 and other working data,
  then H4 is measured ONCE as the gate (catch ≥ 90% and FP ≤ 2% together). **H3 is demoted to working data for the
  credential-slot category** (holdout log).
- 2026-10-02 — **R9 built: `recall/index_version.py`.**
  - **What it does:** every version gets one entry in its own transaction (`write_version` calls it), sealed under
    the version key's RECALL_INDEX HKDF sub-key (new `SubkeyPurpose` in `encrypt_payload`; it still counts against
    the data key's cap). The AAD binds stream, generation, version id and embedder.
  - **Index text (D1):** nucleus, else support_text; episodes index "".
  - **Embedder check:** a stream indexed with another embedder refuses writes until it is re-indexed.
  - **Tests:** 10, plus 1 in keys.
- 2026-10-02 — **R10 built: `recall/load_index_cache.py`**, plus snapshot sessions in `open_scoped_session`
  (`snapshot=True`: REPEATABLE READ, read only).
  - **Snapshot sessions:** the transaction's first statement sets the isolation, so grants are resolved inside the
    recall snapshot (owner decision 2); a snapshot session's write set is empty.
  - **The cache:**
    - serves only inside a snapshot session, and only for streams granted in that snapshot. An ungranted stream
      refuses the whole call, even when another principal warmed it;
    - erasure: on a shred-epoch change, entries of destroyed keys are evicted before serving;
    - generation or embedder change: the stream is dropped and reloaded;
    - entries are fetched by version id, not by sequence, to avoid missing late commits.
  - **Tested:**
    - every kind pair (25), warm and cold;
    - erasure honoured by two independent caches ("processes");
    - an older snapshot may still see content its own snapshot predates; the next one never does;
    - revocation;
    - LRU cap.
  - **Still to do:** the every-pair suite runs again end to end through `recall_context` at R19.
- 2026-10-02 — **Credential-slot rule `credential-slot-value` added** (D-0011 tightening; no allowlist).
  - **Working data:** 748/750 caught (H3 credential slots were 54–72%); working-negatives FP 0.37% → 0.49%.
  - **H3 other categories and FP:** still pass.
  - **H4 is ready for its single gate measurement**, awaiting the owner's go.
- 2026-10-02 — **H4 measured ONCE (owner go): GATE ITEM 12 FAILED.**
  - **Credential-slot catch:** 222/250 = 88.8% (< 90%).
  - **FP:** 0/400 (≤ 2%: met); no type-annotation false positives.
  - **Other groups:** all ≥ 99% except generic:dotenv/assignment 49/50.
  - **Pre-decision applied:** no rule change, no re-measurement; H4 demoted (all categories).
  - **Misses:** 25/28 are one context (Elixir triple-quoted heredoc), 1 is the digit gap, 2 are partial.
  - **Open:** gate item 12 needs a fresh sealed H5 after any rule work. **Owner decision needed.**
  - **Also recorded:** A-0044 (the digit requirement's gap); D-0024 clarification (erasure takes effect at the next
    recall's snapshot, so a recall already in flight may still see the entry).
- 2026-10-02 — **R11 built: `recall/rebuild_index.py`.**
  - **Behaviour:** an identical rebuild writes nothing; a different or missing entry writes generation g+1 and
    switches (reason `rebuild`); an embedder change does a full re-index into g+1 (reason `embedder_change`), after
    which the old model may no longer write.
  - **Shredded versions** are unverifiable and get no new entry.
  - **Back-fill:** generation 1 is back-filled for streams indexed before R9.
  - **Locking:** the append lock is held throughout, so no version can land mid-rebuild.
  - **Refactor:** `index_version` gained an explicit generation, `entry_plaintext()` and `open_plaintext()`.
- 2026-10-02 — **Credential-slot rule revised on working data (H4 now working).**
  - **Fixes:**
    - the root cause of the 1-character exposures (a string prefix matched without its quote);
    - nested dotenv (the entropy layer judges the last right-hand side of a chained assignment);
    - multi-line literal openers from 20 language references (`scripts/build_credential_slot_regex.py`).
  - **Working data:** working set 499/500, H3 250/250, H4 249/250; FP 0.49% working, 0/400 on H4 negatives.
  - **H5:** being built blind by a separate session. Measured once after sealing.
- 2026-10-02 — **Flaky-test hunt: `test_killed_runs_leave_no_database_behind`** timed out once in the full suite
  (SIGTERM'd dry-run runner not exited in 120 s).
  - **Reproduced** with a parallel kill sweep. **Root cause, from the server log** ("still waiting for backend with
    PID N to accept ProcSignalBarrier"; that backend later logged "canceling authentication due to timeout"):
    1. the runner's signal handler raised SystemExit mid psycopg handshake, leaving a half-authenticated backend;
    2. `DROP DATABASE ... WITH (FORCE)` waits for every backend to accept its barrier, and an authenticating
       backend does not (FORCE cannot terminate it: it has no database yet);
    3. so the drop waited for `authentication_timeout` (60 s each).
  - **Classified: a product race** in the runner's abort path.
  - **Fix:** cooperative aborts. The signal is recorded at once; the run stops at the next safe point (between
    families or reps); a second signal exits at once.
  - **Deterministic test:** a signal delivered mid-"handshake" never interrupts it.
  - **Stress:** 48 killed runs, 0 hangs, 0 barrier waits.
  - **EXP-0001 runner:** has the same handler but uses SQLite, so it is unaffected.
  - **Noted, not fixed (out of scope):** runs STARTED simultaneously can fail with "tuple concurrently updated",
    because concurrent migrations GRANT on cluster-wide roles. Runs are sequential in practice.
- 2026-10-02 — **H5 measured ONCE: GATE ITEM 12 FAILED AGAIN.**
  - **Credential-slot catch:** 185/250 = 74.0%.
  - **FP:** 2/400 = 0.5% (met; url-userinfo docstrings, no type annotations); other groups ≥ 99%.
  - **Demoted; no patching on H5.**
  - **Misses (all four contexts missed entirely):** Bash `read -d '' NAME <<'EOF' || true`, Perl heredoc opener
    ending in `;`, a .NET `key="…SigningKey" value="…"` attribute pair, redis-cli `AUTH user pass`. 9 of the 12
    multi-line contexts were caught.
  - **Owner decision needed** on how to proceed with gate item 12.
- 2026-10-02 — **Gate item 12 re-planned (owner).**
  - **Rule:** an additive proximity rule (credential word → high-entropy digit-bearing token in the same statement,
    or on the first non-empty line after a string or heredoc opener).
  - **Targets, PRE-REGISTERED in the holdout log before H6 exists:** common ≥ 95%, long-tail ≥ 80%, FP ≤ 2%.
  - **H6:** built blind and measured once. Pass closes item 12; fail means no H7, back to the owner.
- **KNOWN LIMITATION (for later, owner 2026-10-02):** concurrent migrations (e.g. EXP runs started at the same
  instant) can fail with "tuple concurrently updated", because migrations GRANT on cluster-wide roles. Runs are
  sequential today.
- 2026-10-02 — **Proximity layer built** (`ledger/find_credential_proximity.py`, layer 4 of `strip_secrets`).
  - **Working data:** 99.6–100% credential-slot catch on working, H3, H4 and H5; FP 0.43% over 1611 negative
    documents.
  - **H6:** being built blind; measured once against the pre-registered targets.
- 2026-10-02 — **Proximity layer hardened before H6.**
  - **Speed:** quadratic time fixed (it stalled the full suite).
  - **False positives on the repository's own JSON/docs:** cut from 208 files to 0 with five tightenings (whole
    words, same field, nearest label, lowercase paths, identifier tails); working catch unchanged.
  - **Owner note:** the H6 FP target (code negatives) does not cover JSON/log-style agent data.
- 2026-10-02 — **H6 measured ONCE: revised gate item 12 FAILED.**
  - **Results:** common 83.8% (< 95%); long-tail 46.7% (< 80%); FP 1.0% (met); other groups ≥ 99%.
  - **Pre-decided:** no H7; the result goes to the owner.
  - **Causes:**
    - the repository-FP tightenings killed attribute pairs;
    - the credential word is on an earlier line;
    - vocabulary (`pass:`, `htpasswd`);
    - two contexts have no credential word at all.
  - **Gate item 12 remains open; owner decision needed.**
- 2026-10-02 — **R12 built: `recall/freeze_snapshot.py`.**
  - **What it reads:** per-stream positions, shred epochs, projection and index generations and the embedder, all
    read once inside a grant-confirmed snapshot session.
  - **Tested:** concurrent two-stream writes never tear the cut (A-0037).
  - **R13 is BLOCKED** on an owner decision: D-0017 (contested heads withheld from recall-eligible reads,
    `read_heads`) vs D-0025 §2 (contested heads eligible, shown as contested).
- 2026-10-02 — **Owner decisions.**
  - **Gate item 12 NOT MET, RISK ACCEPTED BY OWNER (2026-10-02)**, with H6: common 83.8%, long-tail 46.7%, FP
    1.0%. No new target definition.
  - **Compensating controls → D-0029** (accepted in principle; design details PROPOSED, owner review before code):
    a report-leaked-secret suppression path; a rotate-credential notice for every detected secret. A-0045.
  - **Future Phase 4/5 item:** a second-opinion local secret classifier on a fresh holdout.
  - **R13 follows D-0025 (amendment 1):** contested beliefs are labelled, carry their contradicting evidence, rank
    below uncontested ones, and are never phrased as fact. `read_heads` and Phase 2 reads are unchanged (D-0017
    cross-reference).
- 2026-10-02 — **Process note:** docs commit 8464f3b went in on `check_structure` and a secret scan only, without the
  required full pytest gate. The full suite was run right after: 1195 passed.
- 2026-10-02 — **R13 built: `recall/merge_scopes.py`.**
  - **Eligibility:** active, contested and fallback heads plus active episodes, from each stream's active projection
    generation at the snapshot position. Lost and superseded heads are excluded.
  - **Contested heads** carry their contradicting evidence (the agreeing contradiction proposals).
  - **Scope levels** are ranked narrowest first.
  - `read_heads` is unchanged (a test asserts it still withholds contested heads).
  - **Still to come:** ranking below uncontested (R15) and never-as-fact rendering (R16/R21).
- 2026-10-02 — **R14–R17 built.**
  - **R14 identity narrowing:** D1 placement of file (with code) and entity (after code) in the relaxation order.
  - **R15 ranking:** contested always below uncontested.
  - **R16 frame assembly:** contested items labelled, with their contradicting evidence; a deterministic CBOR
    frame; the query text never in the frame.
  - **R17 coverage:** a contested top item is never strong.
  - **D1 reading to confirm (owner):** D-0025 §5 "pinned whatever the budget" vs the EXP-0004 budget fixed at
    10 items / 4,000 chars. Implemented as: contested items are pinned against relevance PRUNING but count against
    the budget and fill last, so a tight budget can drop them.
- 2026-10-02 — **R18 BLOCKED (owner decision, D3):** where the ContextAssembled trace lives. Proposed as D-0025
  amendment 2: content parts in each item's source stream, written by a recall-trace service principal (contributor
  keys stay local, so erasure and forget_period reach them); query part and header in the requester's home stream;
  one transaction. **R19 and R20 depend on it.** Not blocked: the interface work (R1–R5, R21–R24) and EXP-0004
  preparation.
