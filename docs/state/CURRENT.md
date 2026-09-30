# Current state

**Phase:** 1 — ledger and scopes (BUILDING)
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
- Next: #6 with the corpus (provider-format research in hand, see evidence/A-0010-provider-formats.md), then 14, 7, 8.
- To run DB tests: `docker compose up -d --wait`, then `pytest`.
- **Every clone:** `git config core.hooksPath scripts/hooks` (D-0010 pre-commit secret scan; needs `.venv`
  with `google-re2`). Temporary scanner `scripts/scan_staged_secrets.py` is replaced by #6.

## Phase 1 gate (FROZEN by owner 2026-09-30)
Phase 1 is done when all of these pass on the Docker Postgres (`postgres:17.11`):
1. Idempotent retries return the original commit, with no duplicates.
2. AS_OF(N) snapshots are stable (MNEXA ADR-0010 R-18: byte-identical after later commits).
3. The chain verifier detects tampering, including a full-stream rewrite (caught by checkpoints).
4. Shredding makes payloads unreadable while the chain still verifies.
5. The cross-scope read test fails as expected (D-0005 S-3). *Status: suite in place and passing through the
   door (#10: 25 kind pairs + reuse/rollback/revoke); re-run at the gate with real appended events.*
6. A-0007 throughput is measured and the result recorded (pass/fail against the provisional target is reported, not hidden).

## Standing instructions (owner, 2026-09-30)
- **A-0007 / gate item 6:** the throughput test must run with the **production connection setup**.
  Decide on `psycopg_pool` (new dependency, needs an ADR) **before** gate item 6 runs; otherwise
  the result is reported explicitly as **unpooled**.
- **A-0017 / INDEX #6:** when #6 starts, evaluate `google-re2` against Python `re`: identical
  semantics to gitleaks' rules, and no catastrophic backtracking on untrusted input. Bring the
  comparison to the owner as part of A-0017.

## Open questions
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
