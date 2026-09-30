# Current state

**Phase:** 1 — ledger and scopes (PLANNING; awaiting owner approval, no code yet)
**Last updated:** 2026-09-30

## Done
- Repo constitution, rules, decision and assumption registers, structure checker.
- Final build spec pasted into `docs/spec/SPEC.md` (uncommitted at time of writing).
- Phase 1 planning: proposed ADRs D-0002 (event envelope), D-0003 (seal chain),
  D-0004 (crypto-shredding key granularity), D-0005 (scope isolation).
- New assumptions A-0007 … A-0015 recorded.
- Planned Phase 1 files listed in `docs/modules/INDEX.md` (status: planned).

## Next
1. Owner reviews D-0002 … D-0005 (D-0004 and D-0005 are D3: explicit approval required).
2. Owner answers the open questions below.
3. Only then: implement Phase 1 files in the order given in INDEX.md.

## Open questions for the owner (blocking Phase 1 code)
1. **Phase 1 gate.** The phase table in SPEC.md is embedded content and did not paste as text.
   The gate for Phase 1 is therefore not written anywhere. A candidate gate is proposed below;
   it needs confirmation or replacement.
2. **Spec conflict: attachment dedup vs. shredding/isolation.** SPEC says attachments are
   "content-addressed, deduplicated". Global dedup by plaintext fingerprint lets one scope
   probe another's content and makes a shared blob impossible to shred. D-0004 proposes
   dedup *within a key only*. Needs owner confirmation.
3. **Spec conflict: monthly partitioning vs. gapless sequence.** Postgres cannot enforce
   `UNIQUE(stream_id, commit_seq)` or `UNIQUE(stream_id, idempotency_key)` across a table
   partitioned by month. D-0003 proposes no partitioning in Phase 1 and a separate ADR when
   volume requires it.
4. **Spec conflict: "deleting a scope removes its stream" vs. insert-only ledger.** D-0004
   proposes shred + deletion marker (rows remain as ciphertext); physical removal of a fully
   shredded stream is an out-of-band admin operation, if ever.
5. **Task scope.** SPEC lists Task as a scope and says "one stream per scope". D-0005 proposes
   tasks are *not* streams but a `task_id` inside the owning stream. Departs from the literal spec.
6. **MNEXA ADR-0010 ("four time concepts").** Not in this repo; the envelope has only
   `occurred_at` / `recorded_at` until the owner supplies ADR-0010's text.
7. **Dependencies (D2).** `psycopg` 3 and `cryptography`. Postgres minimum version
   (local is 14.17). Python minimum: `uuid.uuid7` needs 3.14; pyproject says >=3.11.
8. **Attachment backend for Phase 1.** Local filesystem store vs. S3-compatible (MinIO) from day one.
9. **Tests need a real Postgres** (RLS can't be faked). Local Homebrew Postgres vs. docker in CI.

## Candidate Phase 1 gate (PROPOSED, not frozen)
All must pass on a real Postgres:
- Append → read round-trip for every event type and payload type.
- Gapless `commit_seq` per stream under 16 concurrent writers; zero gaps, zero duplicates.
- Idempotent retry returns the original event; same key with a different request hash is rejected.
- `verify_chain` passes on clean streams and detects each of: edited payload, edited metadata,
  deleted row, reordered rows, a whole stream rewritten with recomputed hashes (via checkpoint).
- App role cannot UPDATE / DELETE / TRUNCATE events (tested, not assumed).
- Cross-scope read attempt returns zero rows for every scope kind pair (adversarial RLS suite).
- After shredding: payload unreadable, chain still verifies, deletion marker present.
- Secret-stripping corpus: see A-0010 threshold.
- AS_OF(N) read returns exactly events 1..N of the stream.

## Blockers
- Owner approval of D-0002 … D-0005 and answers to the open questions above.

## Verification
- `python scripts/check_structure.py` → 0 failure(s), 0 warning(s) (2026-09-30, after planning edits; no source files exist yet, so this checks nothing substantive).
- `pytest` → "no tests ran" (no tests exist yet).
- Nothing committed; changes are in the working tree for owner review.
