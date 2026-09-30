# D-0004: Crypto-shredding key granularity and key custody

- **Status:** accepted (owner, 2026-09-30, explicit D3 approval, with amendments below)
- **Tier:** D3 (privacy boundary)
- **Date:** 2026-09-30
- **Relies on assumptions:** A-0008, A-0009, A-0014, A-0015
- **Related:** D-0002 (`key_id`, AAD, `request_mac`, `attachment_ref`), D-0003 (chain over ciphertext), D-0005 (who may load a key), D-0006 (`cryptography`)

## Amendment history (owner-directed, before acceptance)
1. **Epoch fixed to one calendar month.**
2. **Envelope encryption:** data keys are wrapped by a **per-stream master key**. The proposal
   wrapped data keys directly with one global KEK.
3. **Per-stream system key** for events that are not by or about a person.

## Amendments after acceptance (owner-directed; additive, no accepted text changed)
4. **2026-09-30 — root key custody confirmed and extended.** The root key lives in a local file and
   wraps the stream master keys, as recorded in the accepted Decision table. The owner added:
   - **Separate backup:** the root key is backed up separately from database backups, never
     inside or next to them. A DB backup alone must never be enough to read anything.
   - **Behind an interface:** all root-key access (wrap and unwrap a stream master key) goes
     through one interface, `RootKeyProvider`. Phase 1 ships a local-file implementation, and a
     cloud key service can replace it later without touching callers.
   - **Rotation = rewrap:** rotating the root key means unwrapping every stream master key with
     the old root key and rewrapping it with the new one, in one transaction per batch, recording
     which root-key version wraps each row. Data keys and ciphertext are untouched.
     Shredded (deleted) master keys are not resurrected by rotation.
5. **2026-09-30 — monthly rotation for system keys confirmed** (as already stated in the accepted Decision).
6. **2026-09-30 — shredding is final only at the next root-key rotation.** Deleting a wrapped-key
   row cannot by itself guarantee deletion: the bytes survive in dead tuples until VACUUM, and in
   WAL, replicas and backups, which no in-database scrub reaches. Therefore:
   - A shred (key-row deletion + deletion marker) makes data **unreadable to the running system
     immediately**, and **unrecoverable at the next root-key rotation**.
   - Root-key rotation (`keys/rotate_root_key.py`) steps:
     1. Rewrap every *surviving* stream master key under a new root-key version.
     2. Then destroy the old root-key version, **including its separate backup** (D-0004
        amendment 4). The code destroys the local file; destroying the backup copy is an operator
        step that the rotation run records as a required, confirmed action.

     Any wrapped master key recovered from WAL, a replica or a backup was wrapped under a destroyed
     root version, so it can never be unwrapped again.
   - Rotation runs **at least monthly**, and **on demand** for urgent deletions.
   - `keys/shred_keys.py` (#20) and `keys/rotate_root_key.py` (#20a) implement this together.
   - A-0008's test is updated accordingly.

   *Open issue raised by the builder on 2026-09-30 (data-key shredding not made final by root
   rotation alone): resolved by amendment 7.*
7. **2026-09-30 — master-key rotation after data-key shredding (owner-approved, D3).**
   - **Marking:** any shred that deletes data keys in a stream (person erasure, forgetting months)
     marks that stream for master-key rotation.
   - **Once per cycle:** before the next root-key rotation, each marked stream gets **exactly one**
     master rotation per rotation cycle, however many shreds it had.
   - **Order:**
     1. Create the new master key.
     2. Rewrap all surviving data keys under it.
     3. Delete the old master key.

     This runs as one transaction or as an idempotent, resumable job.
   - **Crash safety:** a crash must never leave a data key wrapped only under a deleted master key.
   - **Audit:** each step is a ledger event in the org stream.
   - **Caches:** any in-process cache of unwrapped master or data keys is invalidated on rotation.
   - **Why this closes the gap:** a deleted data key recovered from a backup was wrapped under the
     *old* master key. That master key then exists only in backups, wrapped under a root version
     that the following root rotation destroys. Neither can be unwrapped again.
   - **Implementation:** `keys/shred_keys.py` (#20), `keys/rotate_master_key.py` (#20b) and
     `keys/rotate_root_key.py` (#20a) are built together.
   - **Checks:**
     - A-0008's extended test covers person and month erasure.
     - A crash test kills the rotation job mid-way and proves that resuming loses no surviving data key.

8. **2026-09-30 — cadence superseded by D-0014 amendment 2:** root rotation runs **weekly**, plus on demand when a
   grace period ends. Grace period plus rotation completes within 30 days.

## Context
SPEC open decision: "Crypto-shredding key granularity: per user, per project, or per memory."
The hard case none of those alone covers: erasing **one person's contributions inside a shared
scope** (Alice's messages in project P) without destroying the project's history, and without
making her content unreadable to teammates while she is active.

## Options considered
1. **One key per stream.** Scope deletion is trivial. Can't erase one person inside a shared scope.
2. **One key per user only.** Shared-scope content becomes unreadable to members, and project
   data has no owner key.
3. **One key per memory / per event.** Key store as large and hot as the ledger. Scope
   deletion means millions of key destructions.
4. **One key per (stream, subject, month).** Chosen.

Custody:
- **a. Keys in plain DB rows.** Backups keep them forever, so shredding is fake.
- **b. Keys wrapped by a key held outside the DB.** Chosen, as a two-level hierarchy (amendment 2).
- **c. External KMS per key.** Heavy for local-first installs. It remains a drop-in upgrade
  because `key_id` is opaque.

## Decision

**Key hierarchy (three levels):**

| Level | Key | Stored | Destroying it erases |
|---|---|---|---|
| Root | Root KEK, one per installation | Local file outside the DB and repo (Phase 1) | Everything (installation teardown only) |
| Stream | **Stream master key**, one per stream | `keys.stream_master_keys`, wrapped by the root KEK | **The whole stream**: every data key under it |
| Data | **Data key (DEK)** per (stream, subject, calendar month) | `keys.data_keys`, wrapped by that stream's master key | That subject's content in that stream for that month |

- **Subject:**
  - **a person**, for events by or about that person;
  - **otherwise the stream's system subject**, which gives the per-stream *system key* of amendment 3.

  At intake, `statement` and `message` events with `actor_kind = person` get subject = that
  person, and a caller may name a person subject explicitly (e.g. an event *about* a client).
  Everything else gets the system subject.
- **Month:** the UTC calendar month of the event's `recorded_at`. It is used only to pick a key,
  never to order anything (D-0002). The system key rotates monthly too, so "forget everything in
  stream S before month M" is expressible.
- **Cipher:** AES-256-GCM, 96-bit random nonce, AAD per D-0002. Each DEK derives two HKDF sub-keys:
  one for `request_mac` and one for `attachment_ref`.
- **Nonce budget (A-0015):** a per-DEK encryption counter. Append refuses to encrypt past 2^28
  uses under one DEK; with monthly rotation this should never trigger.
- **Attachments:** encrypted under the event's DEK and deduplicated only within that DEK
  (accepted departure from SPEC; SPEC updated).

**Shredding operations:**

| Operation | Destroys | Marker |
|---|---|---|
| Delete a scope | That stream's master key (one row) | A `deletion_marker` in the org stream |
| Erase a person | Every DEK whose subject is that person, across all streams | One `deletion_marker` per affected stream, encrypted under that stream's current system key |
| Forget a period | The DEKs for the chosen months (per subject or all subjects) | A `deletion_marker` in the stream |

Scope deletion writes its marker in the org stream because the stream's own keys are gone.

Rows remain as ciphertext and the chain stays valid (D-0003). Physical row removal is not an app
capability (accepted departure from SPEC). Reading a shredded event returns an explicit
`Shredded` result, never an error and never partial plaintext.

## Why this one
Option 4 is the only one satisfying both scope deletion (SPEC Scopes rule 4) and per-person erasure
in shared scopes. It collapses to one key per stream per month where no person is involved. The
per-stream master key (amendment 2) makes scope deletion a single-row destruction instead of
enumerating every DEK. The monthly epoch (amendment 1) gives time-bounded forgetting and keeps
every DEK's use count low.

## Consequences
- Needs `cryptography` (AES-GCM, HKDF; Ed25519 is shared with D-0003); see D-0006.
- The `keys` schema sits behind RLS (D-0005), a second wall behind row isolation. The verifier role
  has no access to it.
- Every read resolves a DEK through its stream master key. A small per-transaction cache is a D1
  detail; the cost is measured in Phase 3 (A-0004).
- Erasing a person does **not** erase facts others stated about them, and it does not erase
  derived memories. Derived-store rebuild without shredded events is a later-phase functionality.
- Plaintext headers survive shredding by design (A-0009). After erasure the person's opaque
  `actor_id` remains as a pseudonym.
- Backup retention of the `keys` schema becomes a privacy promise ("erased within N days", A-0008).

## How we'd know it was wrong
- Erasure requests need per-*memory* granularity that per-subject keys can't express.
- Subject assignment at intake misses a person's content often enough to matter (measure on the coding track).
- Key resolution dominates read latency (Phase 3 profiling).
- Shredded key bytes turn up anywhere after the retention window (A-0008 audit).
