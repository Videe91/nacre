# D-0022: How model calls are recorded (and replayed)

- **Status:** accepted (owner, 2026-10-01). **D3 part APPROVED by the owner 2026-10-01: storing full prompts, encrypted and scoped.**
- **Tier:** D2 (persistence format). Recording prompts, which contain user content, is **D3** (privacy).
- **Date:** 2026-10-01
- **Relies on assumptions:** A-0025

## Amendment 2 (2026-10-02): `frame_id` is recorded, not hashed. Corrects the wording of amendment 1
- **The owner's ruling** was "add an optional frame_id field to model-call records". Amendment 1's sentence "and is
  part of the recorded request identity" was added by Claude while writing it up. It is superseded here.
- **Why:** a recorded replay (EXP-0004 `--recorded`, gate items 5 and 13) rebuilds fresh databases, so frame ids
  differ while the prompt bytes do not. With the frame id in the replay key, every N-arm transfer missed its recording
  (found by `tests/eval/test_run_exp0004_replicate.py`).
- **The rule:**
  - `frame_id` is recorded on the call's `result` event, top-level in the body;
  - it is never in the canonical request, so the canonical form stays version 1;
  - the request hash and the replay key cover only what is sent.
- **Owner review:** this restores the ruling as given. The owner reviews it with the next batch.

## Amendment 1 (owner, 2026-10-02): an optional `frame_id` on model-call records
- **The field:** a model call may carry an optional `frame_id` (the sha256 of the ContextFrame its memory section
  was rendered from, D-0025). It is recorded in the call's `result` event and is part of the recorded request
  identity.
- **Absent means absent:** a call without a frame records nothing. Records written before this amendment decode
  unchanged.
- **Why:** D-0028 §3 requires the OpenAI and Anthropic calls of one recall to name the same `frame_id`, and no
  carrier existed.

## Context
The owner requires recorded model responses **stored as ledger events** for everyday tests, and live repeated runs
for the gate.

MNEXA stored only output text, with no prompts, hashes or decoding settings, so none of its runs can be re-executed
without a model (inventory §0.4).

Two more requirements pull on the design:
- MNEXA ADR-0002 treats model output as attributed content: it asserts that the output was produced, not that it is
  true.
- MNEXA ADR-0011 requires model calls inside recall to be recorded with model identity and version.

## Options considered
1. **Cassette files outside the ledger** (VCR-style).
   - Pros: simple.
   - Cons: violates the owner's instruction. Prompts, which carry user content, would sit outside encryption and
     shredding.
2. **A new `model_call` event type.**
   - Pros: clear.
   - Cons: an envelope enum change (D-0002) and a migration. The existing types already fit.
3. **`result` events by the model (recommended).**
   - `event_type = result`, `actor_kind = model`, `actor_model` and `actor_model_version` in plaintext (D-0002
     already carries them for exactly this), `payload_type = trace`.
   - `trust = untrusted`: model output is content, never an instruction.

## Decision (proposed)
Option 3.

### Body (encrypted)
- `purpose` (the seat);
- `request`: the canonical request (D-0021);
- `request_sha256`;
- `response`: `{text, finish_reason, usage, response_id, model_reported}`;
- `status`: `ok | error`, with `error_class` when an attempt failed. Each attempt is a separate event;
- `attempt`;
- `latency_ms`;
- `run_id`: a cycle or sleep-pass id, also set as the envelope `cycle_id`.

### Stream
- The call is recorded in the scope whose content is in the prompt, so it is encrypted under that scope's key and
  shredded with it.
- **Invariant:** one prompt, one scope. `call_model` refuses a request built from events of two streams.

### Secrets
- The body passes through `append_event`, which strips secrets.
- Prompt content was already stripped at intake, so stripping a request should change nothing. If it does change
  anything, the event is marked `redacted: true` and cannot be replayed. That gets a test.

### Size
The body is capped at 1 MiB. Larger bodies go into an attachment (D-0013, text-scanned).

### Replay (`RecordedProvider`)
- It serves a request only from `result` events with `actor_kind = model` and the same `request_sha256` that are
  readable in the session.
- Identical requests (replicates) are returned in their recorded order.
- A miss raises `RecordingMiss`. There is never a live fallback.

### Fixtures
- Everyday tests load recordings from `tests/fixtures/model_calls/*.jsonl`. These hold synthetic data only: requests
  and responses from the owner's L3 run, and MNEXA's stored outputs for L2. They are appended into the test DB as
  `result` events before the test.
- Fixture files are content-hashed in a manifest, like the frozen suite.

### Metering
Usage from every call is summed per run and per task. Cost per task is a Phase 2 gate report item.

## Why this one
- It follows the owner's instruction directly: recordings are ledger events, so they are sealed, scoped, encrypted
  and shreddable.
- It adds no envelope change.
- It gives exact, offline replay.

## Consequences
- **(D3) Prompts that contain user content are stored.** They are encrypted and shreddable in the same scope, so
  erasing a person or scope also erases the prompts and responses about them. After shredding, replay of that run is
  impossible by design.
- The ledger grows by 2 events per flagged episode.

## How we'd know it was wrong
- Replay misses on identical pipeline runs. A-0025 would be invalidated: something non-deterministic has entered the
  prompt, such as timestamps, UUIDs or set ordering.

## Security invariants
Each invariant is checked by the test named after the arrow.

- **SI-5:** a recording is never readable outside its scope → a cross-scope replay test.
- **SI-6:** recorded mode makes zero network calls → the socket is blocked in tests.
- **SI-7:** shredding a scope makes its recordings unreadable → a shred-then-replay test expecting `Shredded`.

## Questions for the owner
1. **(D3)** Approve storing full prompts (encrypted, per scope) as the price of exact replay?
2. Approve `result` plus `actor_kind = model` instead of a new event type?
