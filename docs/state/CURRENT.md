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
- Done: #1 `core/event.py`, #2 `core/db.py`.
- Next: #2a `core/blob_store.py`, #2b `core/root_key_provider.py`, then #3 migrations (#2c waits on D-0008).
- To run DB tests: `docker compose up -d --wait`, then `pytest`.

## Phase 1 gate (FROZEN by owner 2026-09-30)
Phase 1 is done when all of these pass on the Docker Postgres (`postgres:17.11`):
1. Idempotent retries return the original commit, with no duplicates.
2. AS_OF(N) snapshots are stable (MNEXA ADR-0010 R-18: byte-identical after later commits).
3. The chain verifier detects tampering, including a full-stream rewrite (caught by checkpoints).
4. Shredding makes payloads unreadable while the chain still verifies.
5. The cross-scope read test fails as expected (D-0005 S-3).
6. A-0007 throughput is measured and the result recorded (pass/fail against the provisional target is reported, not hidden).

## Open questions
- **D-0008 (encrypted body format) is PROPOSED — owner approval needed before INDEX #2c, #12, #13.**
  Four points flagged in the ADR: payload_type dropped from AAD (conflicts with accepted D-0002),
  added flags byte, header bytes in AAD, own CBOR codec instead of runtime cbor2.
- Resolved 2026-09-30: root key custody (D-0004 amendment 4), system-key monthly rotation
  (amendment 5), identity mapping rule (D-0002 amendment 3), secret rule set (D-0007).

## Blockers
- INDEX #2c, #12, #13 blocked on D-0008 approval. Nothing else blocked.

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
