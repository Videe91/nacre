# D-0004: Crypto-shredding key granularity and key custody

- **Status:** proposed
- **Tier:** D3 (privacy boundary). **Owner must approve explicitly.**
- **Date:** 2026-09-30
- **Relies on assumptions:** A-0008, A-0009, A-0014, A-0015
- **Related:** D-0002 (`key_id`, AAD, `request_mac`, `attachment_ref`), D-0003 (chain over ciphertext), D-0005 (who may load a key)

## Context
SPEC open decision: "Crypto-shredding key granularity: per user, per project, or per memory."
SPEC also says payloads are "encrypted per scope key" and that deleting a scope removes its keys.

The hard case none of those options covers alone: **a person's contributions inside a shared
scope.** Alice writes messages and statements in project P, which her team reads. If Alice asks
to be erased:
- Per-project keys can't remove her content without destroying the whole project's history.
- Per-user keys would make her project events unreadable to her teammates while she's still
  active, because they would be encrypted under *her* key.

Key granularity also sets key count, key-lookup cost on every read, and what "forget before
date X" can mean.

## Options considered
1. **One key per stream (per scope).** Simplest. Deleting a scope = destroy one key. Can't erase
   one person's contributions inside a shared project or team scope.
2. **One key per user only.** Erasing a person is easy. But project and team data has no owner
   key, and shared-scope content becomes unreadable to members whenever keys are per author.
3. **One key per memory / per event.** Finest control. Key count equals event count, so the key
   store becomes as large and hot as the ledger. Deleting a scope means destroying millions of
   keys, and every read needs a key lookup per row.
4. **One key per (stream, data subject, epoch).** The *subject* is the person an event is by or
   about, or the stream itself for impersonal events (CI runs, tool output, agent actions).
   The *epoch* is a rotation period.
   - Delete a scope → destroy every key of that stream.
   - Erase a person → destroy that person's subject keys in every stream.
   - Forget a period → destroy the epochs covering it.
   Key count ≈ streams × active contributors × epochs, which is modest.

Key custody:
- **a. DEKs in plain Postgres rows.** Backups and replicas keep keys forever, so shredding is fake.
- **b. DEKs wrapped by a master key (KEK) outside the database.** Keys in a separate
  `keys` schema, with backup retention for that schema bounded and documented. Destroying =
  deleting the wrapped row. It becomes effective once the backup-retention window passes (A-0008).
- **c. Every DEK held in an external KMS.** Strongest. But it needs a network call per key and
  per-key cost, and it's heavy for local-first coding-agent installs.

## Decision
**Option 4 (per stream × subject × epoch) with custody b (envelope encryption, KEK outside DB).**
- Cipher: AES-256-GCM, 96-bit random nonce, AAD per D-0002.
- Each DEK also derives (HKDF) two sub-keys, one for `request_mac` and one for `attachment_ref`,
  so those die with it.
- Subject assignment at intake:
  - `statement` and `message` events whose `actor_kind = person` → subject = that person.
  - Everything else → subject = the stream.
  - A caller may name a subject explicitly (e.g. an event *about* a client).
- Epoch: the format supports it via `key_id`; Phase 1 uses a single epoch per (stream, subject).
  A rotation policy is a later ADR. Rotation is also forced when a key reaches 2^28 encryptions (A-0015).
- Attachments are encrypted under the same DEK and deduplicated **only within that key**.
  This departs from SPEC's "content-addressed, deduplicated" (CURRENT.md Q2).
- Shredding writes a `deletion_marker` event in the affected stream naming the destroyed `key_id`s.
  The marker is itself encrypted under the *stream* subject key, never under the destroyed one.
- Scope deletion = shred every key of the stream + a deletion marker. Rows remain as unreadable
  ciphertext and the chain stays valid. Physical row removal is not an app capability (CURRENT.md Q4).
- Reads of shredded events return an explicit `Shredded` result, never an error and never partial plaintext.

## Why this one
Option 4 is the only option that satisfies both obligations at once: scope deletion (SPEC Scopes
rule 4) and erasure of one person inside shared scopes. That second case is certain to come
up for user-scoped memory in a team product. It collapses to option 1 for any stream with no
personal content, so the cost is paid only where a person is involved. Option 3's key volume buys
nothing option 4 lacks for the cases SPEC names. Custody b makes shredding real with no external
service, which keeps local installs possible. Custody c stays a drop-in upgrade because `key_id`
is opaque.

## Consequences
- Needs `cryptography` (AES-GCM, HKDF, Ed25519 shared with D-0003). Pending owner approval.
- KEK custody for Phase 1: a local key file outside the repo and DB. Production KEK handling is a later ADR.
- The key table sits behind RLS (D-0005), so a principal can only unwrap keys for readable
  streams. This is a second wall behind row isolation.
- Erasing a person does **not** erase facts other people stated about them, or derived memories.
  Derived stores must be rebuilt without shredded events (SPEC). That rebuild is a later-phase
  functionality; Phase 1 only guarantees ciphertext is unreadable.
- Plaintext headers (opaque ids, types, times) survive shredding by design (A-0009).
- Backup retention of the `keys` schema becomes a privacy promise ("erased within N days").

## How we'd know it was wrong
- Real erasure requests need per-*memory* removal that per-subject keys can't express.
  Then move to option 3 for those event kinds.
- Subject assignment at intake is wrong often enough that erasure misses content. Measure it
  on the coding track: events containing a person's content but keyed to the stream.
- Key lookups dominate read latency (profile in Phase 3).
- Shredded DEK bytes turn up anywhere after the retention window (A-0008 audit).
