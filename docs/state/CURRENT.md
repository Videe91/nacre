# Current state

**Phase:** 1 COMPLETE (tag `phase-1-complete`); Phase 2 — interpretation plane (PLANNING, no code)
**Last updated:** 2026-09-30

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

## Phase 2 gate (owner framing 2026-10-01; margins pre-registered in EXP-0001)
MNEXA is **prior art, not proof** (SPEC amended; D-0016). Phase 2 is done when all of these pass on the Docker
Postgres:
1. **Frozen-suite integrity:** every file in `tests/regression/mnexa/` matches `MANIFEST.json`. Since 2026-10-01 all
   task sets 003–029 and results 001–035 are copied; SQLite states and the 031 workspace are hash-only.
2. **L1 grader parity (0 model calls):** exact reproduction of MNEXA's recorded grades on the stored decisions of
   003–016.
3. **L2 mechanism parity (0 model calls):** MNEXA's stored raw responses (007–016) through Nacre's gates give exactly
   MNEXA's recorded admissions, rejections (with reasons) and fallbacks.
4. **Live comparison (014–016 final pipeline only):**
   - **Setup:** `gpt-4o-mini-2024-07-18`, provider defaults, k = 3, 180 trials per arm. Arms B (fresh MNEXA,
     EXP-0001), C (no memory, same runs) and N (Nacre, same design).
   - **Validity:** B − C ≥ 30 pp (A-0029).
   - **Non-inferiority:** N ≥ B − 9 pooled, and N_set ≥ B_set − 6.
   - **Superiority:** N − C ≥ 54 pooled, and N_set − C_set ≥ 9.
   - **Safety:** zero margin. **No reruns to reach a pass.**
5. **Recorded-mode determinism:** replaying Nacre's live recording reproduces every grade exactly with the network
   blocked.
6. **Belief lifecycle:** MNEXA's 48 lifecycle tests, translated, pass, plus the 4 deviation tests.
7. **Episodes:** each MNEXA ADR-0009 invariant (Q-1…Q-15) has a passing test.
8. **Write gate:** 100% flag recall on the lesson-bearing episodes of 003–016 (A-0028).
9. **Rebuild:** the `interp` projection rebuilt from the ledger is identical.
10. **Shredding and privacy:**
    - erasing a scope or person makes its model-call recordings, proposals and beliefs unreadable;
    - no content plaintext in the projection;
    - SI-1 to SI-7 pass.
11. **Cost reported:** tokens per flagged episode and per task.
12. **Phase 1 gate still passes.**

**Not in the Phase 2 gate:** the checker model. It is deferred to its own experiment and gate, and ships only if it
improves results.

## Standing instructions (owner, 2026-09-30)
- **Models (owner, 2026-10-01):** always pin dated model versions. On a successor, re-baseline both MNEXA's harness
  and the no-memory control before any comparison.
- **A-0007 / gate item 6:** the throughput test must run with the **production connection setup**.
  Decide on `psycopg_pool` (new dependency, needs an ADR) **before** gate item 6 runs; otherwise
  the result is reported explicitly as **unpooled**.
- **A-0017 / INDEX #6:** when #6 starts, evaluate `google-re2` against Python `re`: identical
  semantics to gitleaks' rules, and no catastrophic backtracking on untrusted input. Bring the
  comparison to the owner as part of A-0017.

## Open questions
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
    - **Open task:** identify this flaky test (suspect a timing-sensitive test on a cold container).
