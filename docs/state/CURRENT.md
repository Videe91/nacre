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

## Phase 1 gate (FROZEN by owner 2026-09-30)
Phase 1 is done when all of these pass on the Docker Postgres (`postgres:17.11`):
1. Idempotent retries return the original commit, with no duplicates.
2. AS_OF(N) snapshots are stable (MNEXA ADR-0010 R-18: byte-identical after later commits).
3. The chain verifier detects tampering, including a full-stream rewrite (caught by checkpoints).
4. Shredding makes payloads unreadable while the chain still verifies.
5. The cross-scope read test fails as expected (D-0005 S-3).
6. A-0007 throughput is measured and the result recorded (pass/fail against the provisional target is reported, not hidden).

## Open questions (non-blocking now; each blocks the file named)
- **Secret-detection rule set** (blocks INDEX #6 `strip_secrets`). The owner said "established
  rules". Which set (e.g. gitleaks' rule set vendored as data vs. the `detect-secrets` library) is a
  dependency choice to bring to the owner when #6 starts.
- **Encrypted body serialization** (blocks INDEX #12 `encrypt_payload`). Byte format inside the
  ciphertext is not yet decided; this is a persistence format (D2), so a short ADR is needed first.
- **Root KEK and stream master key custody detail.** D-0004 records "stream master keys wrapped by a
  root KEK in a local file". The owner's amendment named the per-stream master key but not how that
  key is itself protected; the root-KEK layer carries forward the proposal's custody (b). Owner to confirm.
- **System-key rotation.** D-0004 applies the monthly epoch to system keys as well as person keys
  (read from "time period fixed to one calendar month"). Owner to confirm.

## Blockers
- None for INDEX #1–#5.

## Verification
- See the latest entry in "Session log" below.

## Session log
- 2026-09-30: planning accepted and committed.
