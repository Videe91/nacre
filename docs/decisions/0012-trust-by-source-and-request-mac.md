# D-0012: Trust by source, and the idempotency request MAC

- **Status:** proposed — awaiting owner approval (part A is D3)
- **Tier:** D3 (part A: security boundary, the injection firewall); D2 (part B: persistence format and
  idempotency semantics)
- **Date:** 2026-09-30
- **Relies on assumptions:** A-0012
- **Related:** D-0002 (`trust` is derived from `source` at intake; `request_mac`), D-0003 (append
  transaction), D-0004 (keys), MNEXA ADR-0018 (idempotent writes)

## Context
`ledger/append_event.py` (INDEX #14) cannot be written without two choices that no ADR makes:
- **A.** D-0002 says intake derives `trust` from `source`, but no ADR or SPEC line gives the mapping.
  SPEC only says git, CI and review webhooks are trusted outcome signals, and that untrusted content
  is never an instruction. `trust` feeds that firewall, and `occurred_at_basis = observed` requires
  `trusted` (D-0002).
- **B.** D-0002 defines `request_mac` as "HMAC of the plaintext request", but not which bytes make up
  "the request". That decides when a retry counts as the same logical write (return the original)
  and when it conflicts (reject).

## Part A: trust by source

### Options
1. **Trusted = machine sources Nacre integrates directly, per SPEC: `git`, `ci`, `review`, `system`.
   Untrusted = `chat`, `web`, `tool`.** `tool` output is untrusted because it carries whatever the tool
   read (web pages, files, command output), which is exactly where injected instructions arrive.
2. **Everything untrusted except `system`.** Safest. But it makes `observed` world time impossible
   for git and CI events, which SPEC treats as trusted outcome signals.
3. **Caller-supplied trust.** Rejected: D-0002 forbids the caller from setting trust.

### Proposed: option 1
Mapping: `git`, `ci`, `review`, `system` → trusted; `chat`, `web`, `tool` → untrusted.

Honest limit: until principal authentication exists (interface layer, A-0012), `source` is asserted by
the calling app. A-0012 already records that the app is trusted to state who is calling; this extends
it to what the source is. The authentication ADR must revisit it.

## Part B: the request MAC

### Options
1. **MAC over the caller's request as submitted, before secret stripping.** It covers every
   caller-supplied field: stream, context ids, event and payload types, actor, source, `occurred_at` and
   its basis and precision, `caused_by`, `cycle_id`, config version and mode, body, subject. It excludes
   everything intake assigns (`event_id`, `recorded_at`, `committed_at`, `commit_seq`, `trust`, `key_id`).
   It is serialised as deterministic CBOR (D-0008 codec), and uses the HKDF sub-key of the data key
   that encrypted the **original** event (D-0004).
2. **MAC over the stored body after stripping.** Two different secrets in the same slot would then
   collide as "the same request".
3. **Plain hash.** Rejected by D-0002: it survives shredding.

### Proposed: option 1
- **Semantics:**
  - Same `(stream_id, idempotency_key)` and equal MAC → return the original event unchanged, with no
    new row (gate item 1).
  - Same key, different MAC → reject with an idempotency conflict.
- The MAC is always computed under the original event's data key, looked up by its `key_id`, so a
  retry that crosses a month boundary is still recognised.

## Consequences
- The trust mapping becomes part of the injection firewall and is tested per source.
- A retry must resend the identical request, including `occurred_at`. Intake-assigned times never
  enter the MAC, so they cannot make a retry look different.
- If the original event's key has been shredded, a retry can't be verified. It is rejected as a
  conflict, never silently accepted.

## How we'd know it was wrong
- A source classified as trusted turns out to carry third-party text (e.g. review comments quoting
  web content).
- Retries in real agents differ in fields the MAC covers but the caller considers irrelevant, causing
  spurious conflicts.
