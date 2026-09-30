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
  #3 `schema/apply_migrations.py` + SQL 0001–0003.
- Next: #2c/#2d CBOR encoder/decoder (D-0008), then #4 `encode_envelope`, #5 `seal_event`.
- To run DB tests: `docker compose up -d --wait`, then `pytest`.

## Phase 1 gate (FROZEN by owner 2026-09-30)
Phase 1 is done when all of these pass on the Docker Postgres (`postgres:17.11`):
1. Idempotent retries return the original commit, with no duplicates.
2. AS_OF(N) snapshots are stable (MNEXA ADR-0010 R-18: byte-identical after later commits).
3. The chain verifier detects tampering, including a full-stream rewrite (caught by checkpoints).
4. Shredding makes payloads unreadable while the chain still verifies.
5. The cross-scope read test fails as expected (D-0005 S-3).
6. A-0007 throughput is measured and the result recorded (pass/fail against the provisional target is reported, not hidden).

## Standing instructions (owner, 2026-09-30)
- **A-0007 / gate item 6:** the throughput test must run with the **production connection setup**.
  Decide on `psycopg_pool` (new dependency, needs an ADR) **before** gate item 6 runs; otherwise
  the result is reported explicitly as **unpooled**.
- **A-0017 / INDEX #6:** when #6 starts, evaluate `google-re2` against Python `re`: identical
  semantics to gitleaks' rules, and no catastrophic backtracking on untrusted input. Bring the
  comparison to the owner as part of A-0017.

## Open questions
- **D-0004 open issue (blocks #20/#20a, not before):** root rotation makes master-key shredding
  final but not data-key shredding (person erasure, forget-month): a recovered data key still
  unwraps under its surviving, rewrapped master key. Proposed: a data-key shred also rotates that
  stream's master key before the next root rotation. Owner to decide (D3).
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
