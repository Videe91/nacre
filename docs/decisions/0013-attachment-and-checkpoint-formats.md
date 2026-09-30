# D-0013: Attachment blob format and checkpoint signature format

- **Status:** accepted (owner, 2026-09-30, with additions below)
- **Tier:** D2 (persistence formats)
- **Date:** 2026-09-30
- **Relies on assumptions:** A-0008, A-0013, A-0015
- **Related:** D-0002 (`attachment_ref`, `attachment_sha256`), D-0003 (checkpoints: cadence, separate process,
  key outside the DB), D-0004 (attachments under the event's data key, dedup within that key), D-0005 amendment 2
  (checkpointer role), D-0006 (local disk behind an interface), D-0008 (body ciphertext format)

## Amendment history (owner additions, before acceptance)
1. **Attachments are written before the event commits.** Orphan files are harmless and garbage-collected; an event
   must never point to a missing file.
2. **Every attachment read verifies its fingerprint:** the stored blob against `attachment_sha256`, and the
   plaintext against `attachment_ref`. The 16 MiB Phase 1 limit stands.
3. **Every checkpoint carries its signing-key version** (`signing_key_id`, part of the signed bytes).
4. **Assumption A-0020:** the witness file moves off-machine later (separate storage or a write-once bucket).

## Context
Two stored formats are named by accepted ADRs but not specified byte for byte:
- **A. Attachment blobs.** D-0004 says they are encrypted under the event's data key and deduplicated only within
  that key. D-0002 gives `attachment_ref` (HMAC of the plaintext under a data-key sub-key) and `attachment_sha256`
  (SHA-256 of the *encrypted* blob). No ADR fixes the blob's byte layout, what its AAD binds, or the disk layout.
- **B. Checkpoints.** D-0003 says a separate process signs stream heads (Ed25519) into `ledger.checkpoints` and an
  append-only file outside the DB. No ADR fixes the signed bytes, the signing-key file, or the external file format.

## Part A: attachment blobs

### Options
1. **Reuse D-0008's ciphertext layout with an attachment-specific AAD.** version | algorithm | flags | key_id |
   nonce | ct+tag, where AAD = `"nacre-attachment-v1"` | attachment_ref | header bytes 0–2.
   - Binding to the ref, not to an event, is what makes dedup possible: one blob can serve several events that
     share the key.
   - It uses the same code path and review as D-0008 and counts against the same per-key nonce budget.
2. **A separate attachment format** (e.g. chunked AEAD for large media). Supports streaming large files, but is a
   second format to own. Not needed while Phase 1 attachments are small.
3. **Store attachments inline in the body.** Contradicts SPEC ("keeps the ledger fast") and D-0006.

### Decision: option 1
- **Layout and AAD:** as in option 1.
- **Ref:** `attachment_ref` = HMAC-SHA256 under the data key's `attachment_ref` sub-key (D-0004); it doubles as
  the blob store key. `attachment_sha256` = SHA-256 of the stored blob bytes.
- **Dedup:** if the ref already exists, the existing blob is reused and *its* sha256 is recorded in the event. The
  store is write-once (`core/blob_store.py`).
- **Size limit:** plaintext ≤ 16 MiB per attachment in Phase 1 (single-shot AEAD). Larger media needs option 2
  later, as a new ADR.
- **Metadata:** description and media type go in the encrypted body's `attachment` map (D-0008), never on disk
  in plaintext.
- **Local-disk layout** (`ledger/local_disk_blob_store.py`): `<root>/<first 2 hex of ref>/<64-hex ref>`, files
  0600, written by temp file + hard link (atomic, and fails if the file exists).

## Part B: checkpoint signatures

### Options
1. **Sign a fixed, domain-separated byte string.**
   `"nacre-checkpoint-v1"` | stream_id (16 bytes) | commit_seq (uint64 BE) | head_hash (32 bytes) |
   signed_at (int64 BE µs UTC) | signing_key_id (length-prefixed UTF-8). Ed25519 signature, 64 bytes.
2. **Sign a JSON document.** Readable, but has canonicalisation pitfalls.
3. **Sign the Postgres row text.** Postgres output formatting is not a contract.

### Decision: option 1, plus
- **Signing key file:** a 32-byte raw Ed25519 private seed in a 0600 file outside the repo and DB, read only by the
  checkpointer. The same permission rule as root keys: loose permissions are refused.
  - `signing_key_id` = the first 16 hex chars of SHA-256 of the raw public key.
  - Verifiers trust only public keys from their own configuration, never from the DB (as migration 0004 notes).
- **External append-only file:** JSON Lines, one object per checkpoint, carrying the same fields as the table row
  (hex for bytes), opened with `O_APPEND`, fsynced per write. Verification treats it as the witness: if a DB
  checkpoint row and the file disagree, the verifier reports tampering.
- **Cadence:** as D-0003 (1,000 events or hourly). Poll interval 60 s (D1).

## Consequences
- One ciphertext code path for bodies and attachments. The attachment nonce budget is shared with bodies (A-0015).
- Checkpoints are verifiable offline from the external file plus a configured public key, without the DB.

## How we'd know it was wrong
- Attachments larger than 16 MiB turn out to be common in coding-agent events (logs, screenshots, archives).
- The external checkpoint file is lost or rotated in ways the verifier cannot tell apart from tampering.
