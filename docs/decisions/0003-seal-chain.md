# D-0003: Seal chain (tamper evidence and gapless sequencing)

- **Status:** accepted (owner, 2026-09-30, with amendments below)
- **Tier:** D2 (persistence format, invariant). The owner may raise it to D3 as "what counts as proof" of integrity.
- **Date:** 2026-09-30
- **Relies on assumptions:** A-0007, A-0013
- **Related:** D-0002 (what is hashed), D-0004 (why the chain covers ciphertext), MNEXA ADR-0010 rule 9 (visibility order)

## Amendment history (owner-directed, before acceptance)
1. Signing key custody for Phase 1: a local file outside the database.
2. Checkpoint cadence: every 1,000 events or hourly, whichever comes first (see Decision).

## Context
SPEC: "prev_hash / hash, fingerprint chain per stream", "gapless sequence per stream", "a nightly
job verifies the fingerprint chain", and shredding must leave "the chain still valid". Choices
still open: chain shape, what bytes are hashed, who computes the hash, how appends are serialized,
and which attacker the chain actually detects.

A plain hash chain stored in the same database only detects *accidental* corruption or a naive
edit. Anyone with write access can rewrite a stream and recompute every hash. So the threat
model is part of this decision (A-0013).

## Options considered
1. **One global chain across all streams.** Single ordering. But every append in the system
   serializes on one lock, and dropping or shredding a scope can never be separated from the others.
2. **Per-stream linear hash chain.** `hash_n = SHA-256("nacre-seal-v1" ‖ prev_hash ‖ encode_envelope(event_n))`,
   with `prev_hash_1` = 32 zero bytes. Simple, and verification is one sequential pass.
   Serializing appends per stream is needed anyway for gapless `commit_seq`. Alone, it cannot
   detect a whole-stream rewrite.
3. **Per-stream Merkle log (RFC 6962 style).** Inclusion and consistency proofs in O(log n).
   Much more code. Its benefits (proofs to third parties without full replay) are not needed by
   any Phase 1–3 consumer.
4. **Option 2 + signed checkpoints.** Periodically (nightly, and on demand) record each stream's
   `(stream_id, commit_seq, hash)` head, signed with an Ed25519 key that the database and the app
   role never hold. Checkpoints go to a checkpoint table *and* an append-only file outside the
   database. Verification checks the chain and that it still passes through every checkpoint.

Who computes the hash:
- **a. In a Postgres trigger.** The app can't forge it. But the canonical encoding would then
  live in PL/pgSQL as well as Python, so there are two implementations to keep identical.
- **b. In the app, DB enforces linkage.** Python computes `hash`. A trigger checks
  `commit_seq = previous + 1` and `prev_hash = previous.hash` and rejects otherwise. A wrong
  `hash` value is caught by `verify_chain`.

How appends serialize:
- **i. Mutable `stream_heads` row, `SELECT … FOR UPDATE`.** Standard. But it adds a mutable
  table next to an append-only one, and the app role needs UPDATE on it.
- **ii. `pg_advisory_xact_lock(stream lock key)` then read `max(commit_seq)` via the unique index.**
  No mutable table. The lock is released at commit or rollback automatically. A lock-key collision
  between two streams only adds extra waiting, never incorrectness. `UNIQUE(stream_id, commit_seq)`
  is the backstop.

## Decision
Option 4 (per-stream chain + signed checkpoints), hash computed in app with DB-enforced linkage
(b), serialization by advisory lock (ii). SHA-256 (stdlib) with a domain-separation prefix.

The hash covers **every** envelope field except `hash` itself, including `body_ciphertext`,
`key_id`, `commit_seq`, `recorded_at`, `committed_at`, `attachment_sha256`. It covers ciphertext, not plaintext.
Shredding therefore never breaks verification, and the verifier needs no keys.

Append transaction (one transaction, in `append_event`; `recorded_at` was already set at intake):
take the advisory lock → check idempotency → read the head → assign `commit_seq`, `committed_at`
→ encrypt → compute `hash` → INSERT → commit.

**Visibility order (MNEXA ADR-0010 rule 9).** The advisory lock is held until commit, so the next
appender to the same stream can only read the head *after* the previous event is visible. This
holds **only under READ COMMITTED**. Under REPEATABLE READ or SERIALIZABLE, the snapshot is taken
before the lock wait, so the head read would miss the just-committed row. The append transaction
therefore runs at READ COMMITTED, and a test asserts that. A rolled-back append releases its number
to the next appender, which keeps the sequence gapless. Result: `AS_OF(stream, N)` is immutable
once N is visible (R-18, R-19).

**Checkpoints.** A separate checkpointer process holds the Ed25519 signing key, which lives in a
local file outside the database and outside the repo for Phase 1. The app process and the
`nacre_app` DB role never see it. The checkpointer polls stream heads (interval is a D1 detail,
default 60 s) and signs a new checkpoint for a stream when **either**:
- 1,000 events have been committed since that stream's last checkpoint, **or**
- one hour has passed since that stream's last checkpoint and at least one event has been committed.

Each checkpoint goes to the `checkpoints` table and to an append-only file outside the DB. With
polling, "every 1,000 events" means "within one poll interval of the 1,000th event".

Append-only enforcement: the app role has only INSERT and SELECT on `events`. A trigger rejects
UPDATE, DELETE and TRUNCATE for all roles, the owner included, unless a dedicated migration role
disables it explicitly. Both are tested, not assumed.

Storage: **no monthly partitioning in Phase 1.** A partitioned table cannot enforce
`UNIQUE(stream_id, commit_seq)` or `UNIQUE(stream_id, idempotency_key)` unless the partition key
is in the constraint. That conflicts with SPEC's "partitioned by month" (CURRENT.md Q3).
Partitioning gets its own ADR when volume demands it.

## Why this one
Option 2 is the minimum SPEC asks for. The checkpoints of option 4 add about one small file and
turn "detects accidents" into "detects a DB-level editor without the signing key". That is the
difference between tamper *evidence* and a checksum. Option 3's extra power has no consumer yet;
checkpoints keep the door open because a Merkle log can later be built over checkpoints. Option 1
contradicts per-scope deletion. App-side hashing keeps one canonical encoder (D-0002). The advisory
lock avoids a mutable table in the ledger schema.

## Consequences
- Needs Ed25519 (from `cryptography`, D-0006) and a signing key in a local file outside the DB.
  Production custody is out of scope for Phase 1.
- Throughput per stream is bounded by one serialized transaction at a time (A-0007).
  Cross-stream appends run in parallel.
- `verify_chain` is a pure read with no keys, so the nightly verifier gets a read-only role
  with no access to the key tables (D-0005).
- A tamper between the last checkpoint and now is detectable only as far as the chain alone
  can. The cadence bounds that window to at most 1,000 events or one hour per stream, plus one poll interval.

## How we'd know it was wrong
- The concurrency benchmark (A-0007) misses its target. Then consider per-stream sub-sequences
  or batched appends, under a new ADR.
- A real consumer needs inclusion proofs without full replay (for example, auditors or
  cross-org sharing). Then move to option 3.
- Canonical encoding drift makes `verify_chain` fail on untampered data.
