# D-0008: Encrypted body format (ciphertext header, AEAD, deterministic CBOR plaintext)

- **Status:** accepted (owner, 2026-09-30, with resolutions below)
- **Tier:** D2 (persistence format)
- **Date:** 2026-09-30
- **Relies on assumptions:** A-0015
- **Related:** D-0002 (body contents, AAD), D-0004 (keys, cipher, nonce budget), D-0003 (the ciphertext is sealed)

## Amendment history (owner resolutions, before acceptance)
1. **`payload_type` stays in the AAD.** Its omission from the brief was a mistake; AAD matches
   accepted D-0002, so no D-0002 amendment is needed.
2. **Flags byte:** approved.
3. **Header bytes authenticated:** approved.
4. **Own CBOR codec approved, on three conditions:**
   - **Strict subset only:** text-keyed maps, arrays, text, bytes, integers, booleans, null.
     **No floats.**
   - **A matching strict decoder as its own file**, rejecting non-canonical or out-of-subset input.
   - **Tests:** `cbor2` as a test-only cross-check, `hypothesis` round-trip tests (test-only),
     and frozen byte vectors.

## Amendments after acceptance (owner-directed; additive, no accepted text changed)
5. **2026-09-30 — optional `public_credentials` body key** (D-0007 amendment 4: public credentials are
   not stripped but tagged).
   - **Shape:** `public_credentials`: array of text, the public-credential kinds found in `content`
     (e.g. `stripe-publishable`, `supabase-anon-jwt`, `sentry-dsn-public`). It is encrypted with the
     rest of the body, so which public values an event holds is not plaintext either.
   - **Owner conditions:**
     1. Optional: a body without the key encodes and decodes exactly as before. No body version bump is
        needed, and `content_version` is unchanged.
     2. All frozen byte vectors pass unchanged.
   - **Checks:** the codec is untouched (the frozen CBOR and envelope vectors are unchanged), and a test
     shows a body without the key round-trips byte-identically before and after the amendment.

6. **2026-09-30 — attachment `scan` marker (owner-directed).** The body's `attachment` map gains an optional
   `scan` key:
   - `"text-scanned"`: the attachment was valid, mostly printable UTF-8, and was secret-stripped;
   - `"unscanned"`: binary, stored as given (A-0021).

   Text vs binary is decided by CONTENT, never by the declared media type, so relabelling cannot bypass
   stripping. An absent key encodes exactly as before (amendment 5 conditions).

## Context
D-0002 left the byte format of `body_ciphertext` open. It is a persistence format: every event
ever written must stay decryptable, and `request_mac` (idempotency) is computed over the plaintext
request, so the plaintext serialization must be **deterministic**. Otherwise two retries of the
same logical write would produce different MACs. The owner specified the shape; this ADR fills in
the bytes.

## Options considered
**Plaintext serialization**
1. **Deterministic CBOR (RFC 8949 §4.2.1).** Owner's choice: binary-safe (bytes are native),
   compact, and has a standard determinism profile.
2. **JSON (RFC 8785 JCS).** Readable, but has no byte strings (base64 overhead) and float and
   number canonicalization pitfalls.
3. **Protobuf / MessagePack.** Protobuf needs schemas and a codegen dependency, and neither
   format has a standard deterministic profile.

**CBOR implementation** (this is the part that needs a choice)
- **a. `cbor2` library with `canonical=True`.** Less code. But the exact output bytes then depend
  on a dependency's version. A cbor2 upgrade that changes one encoding detail would silently change
  `request_mac` and break idempotency across the upgrade.
- **b. Our own encoder/decoder for a restricted subset**, ~150 lines in one file. We own the bytes
  forever. It is tested against RFC 8949 Appendix A vectors, and cross-checked against `cbor2` used
  as a **test-only** oracle (dev dependency).
- **c. `cbor2` pinned to an exact version + golden vectors.** Safe, but it freezes a dependency indefinitely.

**Header authentication**
- **i. AAD = owner's field list only.** Then the header's format version, algorithm id and flags
  are not authenticated; flipping a flag bit would go undetected by GCM.
- **ii. AAD = owner's field list + the header prefix bytes.** Chosen: any header tamper fails
  decryption. (The seal chain would also catch it; this makes decryption fail closed on its own.)

## Decision (proposed)

**Byte layout of `body_ciphertext`:**

| Offset | Size | Field | v1 value |
|---|---|---|---|
| 0 | 1 | format version | `0x01` |
| 1 | 1 | algorithm id | `0x01` = AES-256-GCM, 96-bit nonce, 128-bit tag |
| 2 | 1 | flags | bit 0 = compressed (**must be 0 in v1; reserved**); bits 1–7 reserved, must be 0 |
| 3 | 16 | key_id | raw UUID bytes; must equal the envelope's `key_id` |
| 19 | 12 | nonce | random from the OS CSPRNG, per encryption |
| 31 | n + 16 | ciphertext ‖ GCM tag | |

Readers reject:
- an unknown version or algorithm;
- any set flag bit;
- a `key_id` that doesn't match the envelope.

**AEAD:** AES-256-GCM (via `cryptography`, D-0006). Per-key message limit: NIST SP 800-38D caps
random-nonce GCM at 2^32 encryptions per key. Nacre enforces a lower hard cap of **2^28 per data
key** with a counter (A-0015). Monthly data keys (D-0004) keep real use far below it.

**AAD** = the D-0002 canonical encoding of (`envelope_version`, `event_id`, `stream_id`, `key_id`,
`event_type`, `payload_type`), exactly as in D-0002, followed by header bytes 0–2 (version, algorithm, flags).

**Plaintext** = one deterministic-CBOR map (RFC 8949 §4.2.1 core deterministic encoding):
- **Allowed types (amendment 4):** maps with **text keys only**, arrays, text, byte strings,
  integers (−2^64…2^64−1, CBOR major types 0/1), booleans, null.
- **Forbidden:** **floats** (all widths), tags (so timestamps are integer µs), simple values other
  than true/false/null, `undefined`, indefinite lengths, and duplicate map keys. Decimal
  quantities go in as text or scaled integers, decided by the payload type that needs them.
- **Strict decoder:** a separate file. It rejects:
  - out-of-subset items;
  - non-shortest integer or length arguments;
  - map keys not in RFC 8949 §4.2.1 bytewise order;
  - duplicate keys;
  - trailing bytes;
  - invalid UTF-8.

  As a final guard, it re-encodes the result and requires byte equality.
- **Body map v1 keys** (from D-0002's body list):

  | Key | Type | Note |
  |---|---|---|
  | `content_version` | int | Required |
  | `content` | per `payload_type` | Required; null when an attachment carries the content |
  | `person` | map | Optional; `name` / `handle` / `email`; D-0002 amendment 3 |
  | `source_ref` | text | Optional |
  | `attachment` | map | Optional; `description`, `media_type` |
  | `redactions` | array of text | Optional; D-0007 rule ids |
  | `public_credentials` | array of text | Optional; amendment 5 |

**Compression:** none in v1. The flag bit is reserved; a future ADR names the algorithm and sets it.

**Implementation:** option **b**, as two files in `core/`, because both `keys/encrypt_payload.py`
and `ledger/append_event.py` (`request_mac`) need them:
- `src/nacre/core/encode_cbor.py`: the encoder;
- `src/nacre/core/decode_cbor.py`: the strict decoder.

`cbor2` (pinned exactly) and `hypothesis` are test-only dependencies.
The tests cover:
- RFC 8949 Appendix A vectors inside the subset;
- frozen byte vectors for Nacre bodies;
- hypothesis round-trips (encode → decode → equal; decode(non-canonical) → rejected);
- cross-checks that `cbor2` decodes our bytes to the same value, and that `cbor2`'s canonical
  encoding matches ours on subset inputs.

## Consequences
- Every ciphertext is self-describing (version, algorithm, key), so re-encryption or migration
  tools need no side tables.
- The 31-byte header + 16-byte tag add 47 bytes per event.
- Two more core files (encoder, decoder), owned by us, with RFC and frozen vectors as tests.
- No floats in bodies: payload types carrying measurements must choose text or scaled integers.

## How we'd know it was wrong
- Real payloads need a type outside the allowed subset (floats, tagged decimals). That needs a v2 format.
- Cross-language implementations disagree on bytes for the same body (golden-vector test).
- Body sizes make the missing compression hurt (measure in Phase 3).
