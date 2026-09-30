# D-0002: Event envelope (fields, plaintext/ciphertext split, time concepts, canonical encoding)

- **Status:** accepted (owner, 2026-09-30, with amendments below)
- **Tier:** D2 (persistence format)
- **Date:** 2026-09-30
- **Relies on assumptions:** A-0009, A-0010, A-0014
- **Related:** D-0003 (seal chain hashes this envelope), D-0004 (key_id, encryption), D-0005 (stream ownership), D-0006 (uuid7 via Python 3.14)
- **Imports:** MNEXA ADR-0010 (temporal and ordering semantics), read from `../mnexa/docs/decisions/ADR-0010-temporal-ordering-semantics.md`

## Amendment history (owner-directed, before acceptance)
1. **Actor split changed.** The proposal encrypted the actor's model name, version and tool.
   The owner decided: `actor_kind`, model name/version (and tool) stay **plaintext**; only a
   **person's identity** is encrypted.
2. **Time concepts imported from MNEXA ADR-0010** instead of the proposal's two clocks. This
   corrects an error in the proposal. The proposal set `recorded_at` at commit. ADR-0010 defines
   `recorded_at` as capture-boundary receipt time and adds a separate `committed_at`.

## Amendments after acceptance (owner-directed; additive, no accepted text changed)
3. **2026-09-30 — identity mapping only in shreddable events.** Any mapping from `user_id` or
   `actor_id` to a real person's identity (name, handle, email, account) lives **only inside
   encrypted, shreddable events**. It must never appear in a plaintext table: not `scopes`, not
   `scope_grants`, not any future directory or users table. Such a table may hold the opaque id
   and, at most, a pointer (event id) to the identity event.
   *How checked:* a schema test lists every column of every non-`events` table and fails on any
   column whose name suggests identity (`name`, `email`, `handle`, `display`, `login`, …) unless
   it is allow-listed with an ADR reference. Code review of every new table goes against this rule.
   *Consequence:* showing "Alice" for an `actor_id` requires decrypting her identity event.
   After her erasure, only the opaque id remains, which is the point.
   *Note:* D-0005's access grants are keyed by opaque principal ids, so they already satisfy this rule.

4. **2026-09-30 — envelope v2: `trust_basis` (D-0012 amendment 2).**
   - v2 = the v1 field list plus `trust_basis` (`asserted` | `verified`), placed just before `key_id` in
     the canonical order. The v2 AAD equals the v1 AAD.
   - v1 stays defined and frozen. No v1 event was ever written, but the rule is fix-forward, never
     edit a frozen format. Intake writes v2.
   - Migration 0005 adds the column, with a check that v1 rows have no `trust_basis` and v2 rows
     always do.

## Context
The ledger is the only source of truth, and SPEC says typed payloads go into Phase 1 "because
changing the ledger later is painful". Every later layer (seal, shredding, RLS, recall, replay)
reads this envelope. Open questions were:
- which fields are readable without a key;
- how the envelope is serialized for hashing;
- how ids are formed;
- how time is represented;
- how an event belongs to a scope.

## Options considered
1. **Everything in one encrypted blob.** Maximum privacy after shredding. But nothing can be
   indexed (type, time, cycle, causal parent), and RLS, replay and the nightly verifier would all need keys.
2. **Everything in plaintext columns, payload encrypted separately.** Fully indexable. But
   personal identity and an unkeyed hash of the plaintext request survive shredding. A hash of a
   short payload can be brute-forced back to its text.
3. **Split envelope: indexable header in plaintext, no personal identity or content in it;
   content, person identity and anything derived from content encrypted or keyed-hashed under
   the scope key.** Chosen.

Serialization for hashing and AAD:
- **a. JSON + RFC 8785.** Has float and unicode edge cases.
- **b. Fixed-order, length-prefixed binary encoding.** Chosen: trivial and unambiguous in any language.
- **c. Postgres row text.** Postgres output formatting is not a contract.

`event_id`:
- **UUIDv7.** Chosen: sortable, native column, Python 3.14 stdlib `uuid.uuid7()`.
- **ULID.** Needs a text column and a library.
- **DB sequence.** Not globally unique.

Time (MNEXA ADR-0010's options):
- **A. One timestamp.** Loses either late-arrival detection or world time.
- **B. Minimal dual-time plus logical commit order.** Chosen, as extended by ADR-0010's amendments.
- **C. Full bitemporal.** YAGNI, and reachable from B later without migration.

## Decision

### Plaintext header columns (indexable, survive shredding)

| Field | Form | Note |
|---|---|---|
| `envelope_version` | smallint | Currently `1` |
| `event_id` | UUIDv7, generated in app | |
| `stream_id` | opaque UUID | The one owning stream (D-0005) |
| `org_id`, `project_id`, `user_id`, `agent_id`, `task_id` | opaque UUIDs, nullable | Context only; never natural identifiers (A-0009) |
| `commit_seq` | bigint, gapless per stream | ADR-0010 `commit_sequence`; visibility order (see Time) |
| `occurred_at` | timestamptz, **nullable** | World/source time. Absent when unknown; never fabricated |
| `occurred_at_basis` | enum `observed` / `asserted`, null iff `occurred_at` null | No `inferred` basis exists (ADR-0010 rule 3) |
| `occurred_at_precision` | enum `year` / `month` / `day` / `hour` / `minute` / `second` / `millisecond` / `microsecond`, null iff `occurred_at` null | The value is truncated to this precision (ADR-0010 rule 4). The enum values are this ADR's choice; ADR-0010 names only "a granularity enum" |
| `recorded_at` | timestamptz, required | When the event entered Nacre's intake boundary. Set by intake, never by the caller |
| `committed_at` | timestamptz, required | Wall-clock observed inside the append transaction after the stream lock is taken |
| `event_type` | enum: message, action, result, prediction, decision, outcome, statement, memory_event, config_event, correction, deletion_marker | SPEC's list |
| `payload_type` | enum: text, image, audio, diff, table, structured, trace | SPEC's list |
| `actor_kind` | enum: person, agent, model, tool, system | **Plaintext (amendment 1)** |
| `actor_id` | opaque UUID | Stable pseudonymous id; never a name |
| `actor_model`, `actor_model_version` | short text, nullable | **Plaintext (amendment 1)**, e.g. `claude-opus-5-5`. Restricted charset/length at intake so no content can hide here |
| `actor_tool` | short text, nullable | Plaintext; same restriction |
| `source` | enum: chat, git, ci, review, web, tool, system | |
| `trust` | enum: trusted, untrusted | Set by intake from `source`, never by the caller |
| `caused_by` | event_id, nullable | Earlier event in the same stream |
| `cycle_id` | UUID, nullable | |
| `config_version` | short text, nullable; `mode` enum normal / incident / onboarding / exploration | |
| `key_id` | opaque UUID | D-0004 |
| `idempotency_key` | text, opaque, unique per stream | |
| `request_mac` | bytes | HMAC-SHA256 under a key derived from the DEK; dies with the key |
| `attachment_ref` | bytes, nullable | HMAC of attachment plaintext under a DEK-derived key (dedup within key only) |
| `attachment_sha256` | bytes, nullable | SHA-256 of the *encrypted* blob |
| `body_ciphertext` | bytes | AES-256-GCM (D-0004) |
| `prev_hash`, `hash` | bytes(32) | D-0003 |

### Encrypted body
Everything else lives in one AEAD ciphertext:
- the payload content (typed per `payload_type`, with a `content_version`);
- the **person's identity** (display name, handle, email) when `actor_kind = person`;
- source detail (URL, commit hash, CI run id) and, for `asserted` time, the claimed time's source reference;
- the attachment description and media type.

The byte serialization of the body is decided when `keys/encrypt_payload.py` is built; see CURRENT.md.

### Time (from MNEXA ADR-0010; rule numbers refer to that ADR)
- **Only `commit_seq` orders.** `recorded_at` and `committed_at` are descriptive and
  operational. No code path uses them for ordering, preexistence, availability or replay (R-6).
  `occurred_at` implies nothing about causality, availability, influence or ancestry (R-14).
- **`commit_seq` is visibility order** (rule 9). Once `AS_OF(stream, N)` has been exposed, no
  event may later become visible with `commit_seq ≤ N`. D-0003's append protocol provides this.
  Nacre is stricter than ADR-0010: sequences are gapless.
- **`occurred_at` basis must resolve** (rule 2):
  - `asserted` → the `source` plus the source reference in the body.
  - `observed` → admissible only when `trust = trusted`, i.e. a trusted runtime saw it.
- **Late and out-of-order arrival is legal** (rules 14, 18). Ties, coarse values and absent values
  produce no order.
- **Predictions:** `occurred_at` is when the prediction was made. The prediction horizon or
  target time is payload content (R-15).
- **Timestamps are never mutated.** Corrections are new events (rule 17).
- **Deliberate difference from MNEXA:** MNEXA has one sequence for its whole namespace. Nacre
  has one per stream (SPEC). A "knowledge watermark" (rule 12) across several scopes is
  therefore a **vector** `{stream_id: N}`, not a single number. Recall (Phase 3) and cross-scope
  promotion must be designed around that. It is recorded here so nobody assumes a scalar watermark.

### AEAD associated data
AAD = the canonical encoding of (`envelope_version`, `event_id`, `stream_id`, `key_id`,
`event_type`, `payload_type`), so a ciphertext cannot be moved to another row.

### Canonical encoding (b)
`ledger/encode_envelope.py` is the only place that turns an envelope into bytes; the seal and the
AAD both call it.

| Value | Encoded as |
|---|---|
| Timestamps | int64 µs since the epoch, UTC |
| UUIDs | 16 raw bytes |
| Enums | their string value |
| null | a distinct tag |

Field order is fixed per `envelope_version`.

### Intake rules (inside `append_event`)
- Secrets are stripped before encryption (A-0010).
- `trust` is derived from `source`.
- Id fields must be UUIDs.
- Short text fields (`actor_model`, `actor_model_version`, `actor_tool`, `config_version`) must
  match a restricted charset and length.
- `occurred_at` must come with a basis and a precision, and must be truncated to that precision.
- Corrections must set `caused_by`.

## Why this one
Option 3 keeps both SPEC promises. Queries by scope, sequence, cycle, type and time work
without keys. Shredding makes all content and every person's identity unrecoverable. Keeping model
and tool identifiers plaintext (amendment 1) makes "which brain did what" queryable without
keys; that matters for transplant analysis and self-tuning, and model names are not personal
data. ADR-0010's four time concepts are accepted MNEXA semantics that MNEXA's regression tier
depends on; porting them unchanged avoids port drift (A-0003).

## Consequences
- Beyond SPEC's field table, this adds:
  - `envelope_version`;
  - `occurred_at_basis` and `occurred_at_precision`;
  - `committed_at`;
  - split actor columns (`actor_kind`, `actor_id`, `actor_model`, `actor_model_version`, `actor_tool`);
  - `request_mac` and `attachment_sha256`.

  SPEC.md is updated to match.
- Plaintext columns are a privacy boundary. Adding one requires a privacy review (A-0009).
- Knowledge watermarks are per-stream vectors (see Time); Phase 3 must honour this.

## How we'd know it was wrong
- A later phase needs to filter on something that lives only in the body.
- A privacy review finds personal content inferable from headers (A-0009).
- Another implementation produces different canonical bytes for the same event.
- Adapters so rarely supply an `occurred_at` basis that world time is almost always absent (ADR-0010's own falsifier).
