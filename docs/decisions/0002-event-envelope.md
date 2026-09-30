# D-0002: Event envelope (fields, plaintext/ciphertext split, canonical encoding)

- **Status:** proposed
- **Tier:** D2 (persistence format)
- **Date:** 2026-09-30
- **Relies on assumptions:** A-0009, A-0010, A-0014
- **Related:** D-0003 (seal chain hashes this envelope), D-0004 (key_id, encryption), D-0005 (stream ownership)

## Context
The ledger is the only source of truth, and SPEC says typed payloads go into Phase 1 "because
changing the ledger later is painful". Every later layer (seal, shredding, RLS, recall, replay)
reads this envelope, so its shape is the most expensive thing to get wrong. SPEC lists the fields
but leaves open: which fields are readable without a key, how the envelope is serialized for
hashing, how ids are formed, and how an event belongs to a scope.

## Options considered
1. **Everything in one encrypted blob** — the row holds only `stream_id`, `commit_seq`, hash and
   ciphertext; all metadata inside. Maximum privacy after shredding. But nothing can be indexed
   (type, time, cycle, causal parent), RLS and replay need the key, and the nightly verifier would
   need keys to do its job.
2. **Everything in plaintext columns, payload encrypted separately** (SPEC's table taken
   literally). Fully indexable. But plaintext fields such as `actor` (a person's name) and the
   idempotency `request hash` of the plaintext survive shredding. A hash of a short payload
   can be brute-forced back to the text after the key is gone.
3. **Split envelope: indexable header in plaintext, restricted to opaque ids and enums; content
   and anything derived from content encrypted or keyed-hashed under the scope key.**
   Indexable where it matters, and shredding removes all content. Costs a rule: no natural
   identifier (name, email, path, message text) may ever appear in a plaintext field.

For serialization (needed so D-0003 can hash, and so the ciphertext can be bound to its row):
- **a. JSON + RFC 8785 canonicalization.** Readable. But float and unicode edge cases, and it
  needs a library or careful code.
- **b. Fixed-order, length-prefixed binary encoding** (`version ‖ for each field: tag(1) ‖ len(4) ‖ bytes`).
  Trivial to implement identically in any language, no float ambiguity (envelope has none).
- **c. Hash the Postgres row text.** Depends on Postgres output formatting, which is not a contract.

For `event_id`:
- **UUIDv7** (RFC 9562, time-sortable, native `uuid` column). **ULID** (same idea, needs text
  column and a library). **DB sequence** (not globally unique, leaks volume).

## Decision
Option 3 + encoding b + UUIDv7.

**Plaintext header columns** (indexable, survive shredding):

| Field | Form | Note |
|---|---|---|
| `envelope_version` | smallint | New vs. SPEC: lets the format evolve without guessing |
| `event_id` | UUIDv7, generated in app | |
| `stream_id` | opaque UUID | The one owning scope (D-0005). Exactly one per event (A-0014) |
| `org_id`, `project_id`, `user_id`, `agent_id`, `task_id` | opaque UUIDs, nullable | Context ids; never natural identifiers (A-0009) |
| `commit_seq` | bigint, gapless per stream | Assigned at commit (D-0003) |
| `occurred_at`, `recorded_at` | timestamptz, µs, UTC | MNEXA ADR-0010 extra time concepts: **pending**, see CURRENT.md Q6 |
| `event_type` | enum: message, action, result, prediction, decision, outcome, statement, memory_event, config_event, correction, deletion_marker | Exactly SPEC's list |
| `payload_type` | enum: text, image, audio, diff, table, structured, trace | Exactly SPEC's list |
| `actor_kind` | enum: person, agent, model, tool, system | |
| `actor_id` | opaque UUID | Name/model/version live in the encrypted body |
| `source` | enum: chat, git, ci, review, web, tool, system | |
| `trust` | enum: trusted, untrusted | Set by intake from `source`, never by the caller |
| `caused_by` | event_id, nullable | Must be an earlier event in the same stream (checked at append) |
| `cycle_id` | UUID, nullable | |
| `config_version`, `mode` | text id / enum | |
| `key_id` | opaque UUID | Which key encrypts the body (D-0004) |
| `idempotency_key` | text, caller-supplied, opaque | Unique per stream |
| `request_mac` | bytes | HMAC-SHA256 of the plaintext request under a key derived from the scope key. **Not a plain hash** so it dies with the key |
| `attachment_ref` | bytes, nullable | HMAC of the attachment plaintext under the scope key (dedup within key only, see D-0004) |
| `attachment_sha256` | bytes, nullable | SHA-256 of the *encrypted* blob, so blob integrity stays checkable after shredding |
| `body_ciphertext` | bytes | AES-256-GCM (D-0004) |
| `prev_hash`, `hash` | bytes(32) | D-0003 |

**Encrypted body** (one AEAD ciphertext): payload content (typed per `payload_type`, with
`content_version`), actor display name, model name and version, tool name, source detail (URL,
commit hash, CI run id), attachment description and media type, extracted entities (later phases).

**AEAD associated data** = canonical encoding of (`envelope_version`, `event_id`, `stream_id`,
`key_id`, `event_type`, `payload_type`). A ciphertext therefore cannot be moved to another row.

**Canonical encoding** (b): one module (`ledger/encode_envelope.py`) is the only place that turns an
envelope into bytes; D-0003's seal and the AAD above both call it. Timestamps encode as int64
microseconds since epoch UTC; UUIDs as 16 raw bytes; enums as their string value; null as a
distinct tag. Field order is fixed per `envelope_version` and never reordered.

**Intake rules** (enforced inside `append_event`): secrets stripped before encryption (A-0010);
`trust` derived from `source`; plaintext id fields must parse as UUIDs (this blocks natural
identifiers structurally); corrections must set `caused_by`.

## Why this one
Option 3 is the only one that keeps both of SPEC's promises: queries by scope, sequence, cycle,
type and time work without keys (SPEC's indexes), and shredding makes all content unrecoverable.
Option 2 breaks shredding through the actor name and the unkeyed request hash. Option 1 breaks
verification and indexing. Encoding b is a few dozen lines with no ambiguity. UUIDv7 is sortable
and fits a native column. Python 3.14 has `uuid.uuid7()`.

## Consequences
- Adds `envelope_version`, `actor_kind`, `request_mac`, `attachment_sha256` beyond SPEC's table.
  `actor` and `source` detail move into the encrypted body.
- Plaintext columns must be reviewed as a privacy boundary whenever a field is added (A-0009).
- Needs the `cryptography` dependency (AES-GCM, HMAC is stdlib). Pending owner approval.
- `uuid.uuid7` needs Python ≥3.14; pyproject currently says ≥3.11 (CURRENT.md Q7).
- Per-type payload schemas are only versioned in Phase 1 (`content_version`); strict per-type
  validation arrives with the phase that consumes each type.

## How we'd know it was wrong
- A later phase needs to filter or index on something that lives only in the encrypted body. For
  example, recall must filter by model version without keys. Then that field moves to the header
  with a privacy review.
- A privacy review finds personal content inferable from plaintext headers alone (A-0009).
- Another language implementation produces different canonical bytes for the same event.
