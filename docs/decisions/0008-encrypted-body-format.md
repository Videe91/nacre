# D-0008: Encrypted body format (ciphertext header, AEAD, deterministic CBOR plaintext)

- **Status:** proposed — awaiting owner approval before INDEX #12 is built
- **Tier:** D2 (persistence format)
- **Date:** 2026-09-30
- **Relies on assumptions:** A-0015
- **Related:** D-0002 (body contents, AAD), D-0004 (keys, cipher, nonce budget), D-0003 (the ciphertext is sealed)

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

**AAD** = the D-0002 canonical encoding of (`event_id`, `stream_id`, `key_id`, `event_type`,
`envelope_version`), followed by header bytes 0–2 (version, algorithm, flags).

**Plaintext** = one deterministic-CBOR map (RFC 8949 §4.2.1 core deterministic encoding):
- **Allowed types:** maps with **text keys only**, arrays, text, byte strings, integers
  (−2^63…2^64−1), booleans, null, and floats in shortest exact form, with NaN and ±∞ forbidden.
- **Forbidden:** tags (so timestamps are integer µs), indefinite lengths, duplicate keys, `undefined`.
- **Strict decoder:** after decoding, it re-encodes and requires byte equality, so non-canonical input is rejected.
- **Body map v1 keys** (from D-0002's body list):

  | Key | Type | Note |
  |---|---|---|
  | `content_version` | int | Required |
  | `content` | per `payload_type` | Required; null when an attachment carries the content |
  | `person` | map | Optional; `name` / `handle` / `email`; D-0002 amendment 3 |
  | `source_ref` | text | Optional |
  | `attachment` | map | Optional; `description`, `media_type` |
  | `redactions` | array of text | Optional; D-0007 rule ids |

**Compression:** none in v1. The flag bit is reserved; a future ADR names the algorithm and sets it.

**Implementation:** option **b**. The codec lives in `src/nacre/core/deterministic_cbor.py`,
because both `keys/encrypt_payload.py` and `ledger/append_event.py` (`request_mac`) need it.
`cbor2` is added as a dev/test dependency only.

## Points for the owner (differences from the brief)
1. **`payload_type` is dropped from AAD.** Accepted D-0002 put `payload_type` in the AAD; the
   brief's list omits it. Proposal: follow the brief and add a post-acceptance amendment to D-0002.
   Dropping it is safe because `payload_type` is covered by the seal hash (D-0003). Owner to confirm
   or keep it.
2. **A flags byte is added** to carry the reserved compression flag. The brief lists four header
   items plus "a flag reserved".
3. **Header bytes are in the AAD** (option ii).
4. **Own codec** (option b) instead of relying on `cbor2` at runtime.

## Consequences
- Every ciphertext is self-describing (version, algorithm, key), so re-encryption or migration
  tools need no side tables.
- The 31-byte header + 16-byte tag add 47 bytes per event.
- One more core file, the CBOR codec, owned by us, with RFC vectors as tests.

## How we'd know it was wrong
- Real payloads need a type outside the allowed subset (e.g. tagged decimals). That needs a v2 format.
- Cross-language implementations disagree on bytes for the same body (golden-vector test).
- Body sizes make the missing compression hurt (measure in Phase 3).
