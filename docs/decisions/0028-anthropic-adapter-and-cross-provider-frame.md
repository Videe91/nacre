# D-0028: Anthropic adapter, and one ContextFrame across providers

- **Status:** proposed (2026-10-01). Awaiting owner approval. No code until accepted.
- **Tier:** D2 (new provider, dependency, price entries). **Question 1 asks the owner to amend an owner rule**
  (dated pins).
- **Date:** 2026-10-01
- **Relies on assumptions:** A-0024, A-0043 (new)

## Context
- **D-0021** defines the provider interface. The OpenAI adapter is the only one built.
  `scripts/check_structure.py` already reserves `src/nacre/models/anthropic_messages_provider.py` as the only file
  allowed to import `anthropic` (SI-4).
- **The SPEC** says the frontier model is swappable, proven by a cross-provider transplant: the same ContextFrame
  reasoned over by different providers.
- **Facts checked on 2026-10-01** from the bundled Claude API reference (cached 2026-06-24); prices are re-read from
  the live pricing page before any entry is written:
  - **Current Claude model IDs have no date suffix:** `claude-opus-5`, `claude-sonnet-5`, `claude-opus-4-8`, ….
    The reference says these IDs are complete as given, and must not get an invented date suffix.
  - **The one current model with a dated snapshot ID is Haiku 4.5: `claude-haiku-4-5-20251001`** (alias
    `claude-haiku-4-5`).
  - **Sampling parameters:**
    - Haiku 4.5 accepts `temperature`;
    - Opus 5 / Sonnet 5 / Opus 4.7–4.8 / Fable **reject** `temperature`, `top_p` and `top_k` with a 400;
    - Opus 5 thinks adaptively by default (`thinking` can be disabled only at effort ≤ high);
    - assistant prefill returns a 400 on 4.6+ models.
  - **Structured JSON output:** `output_config: {format: {type: "json_schema", …}}` on `messages.create`. The old
    `output_format` parameter is deprecated.
  - **Prices per 1M tokens (reference table, to be re-verified):** Haiku 4.5 $1 / $5; Sonnet 5 $2 / $10;
    Opus 5 $5 / $25.

## The conflict (owner rule vs provider reality)
- **Owner rule:** "Always pin dated model versions" (`core.model_provider.is_dated_pin`). It accepts
  `claude-…-YYYYMMDD`.
- Only Haiku 4.5 has such an ID. Opus 5 and Sonnet 5 cannot be pinned by date, because their IDs are already the
  fixed identifiers.

**Options:**
- **(a) Use `claude-haiku-4-5-20251001` only** (recommended for Phase 3):
  - it satisfies the rule as written;
  - it is the cheapest; it accepts `temperature = 0`, like the OpenAI seat;
  - it is the comparable tier to `gpt-4o-mini-2024-07-18`.
- **(b) Amend the rule** to "an immutable snapshot ID". An undated ID such as `claude-sonnet-5` would be accepted
  when it is listed by the provider's Models API, with its `created_at` recorded on every call.
  - Needed only if a stronger Claude model is wanted.
- **(c) Accept an alias (`claude-haiku-4-5`).**
  - Rejected: an alias can move, and recordings would no longer say which model ran.

## Decision (proposed)
1. **`models/anthropic_messages_provider.py`:** the D-0021 adapter for the Messages API. The official `anthropic`
   Python SDK, exact-pinned, as an optional extra like `openai`.
   - **The SDK's own retries are off** (`max_retries = 0`); `call_model` owns retries.
   - **Request mapping:** `system` → top-level `system`; messages → `messages`; `max_output_tokens` → `max_tokens`;
     a JSON schema → `output_config.format`.
   - **`temperature` is sent only when the request sets it.** A model that rejects it causes a non-retryable
     `ProviderError` before any network call (from a per-model capability row in `prices.json`), never a silent
     drop. This is the same D1 rule as OpenAI's `seed`.
   - **Stop reasons:**
     - `end_turn` maps to complete;
     - `max_tokens` maps to truncated, non-retryable;
     - `refusal` maps to refused, non-retryable, with `stop_details.category` recorded. **No server-side fallbacks:**
       a fallback would change the model mid-call and break the one-pinned-model rule;
     - any other stop reason is an error.
   - **Usage:** `input_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`, `output_tokens` → `Usage`.
     Cost comes from the price table, failing closed (D-0021 amendment 1).
   - **The key** is read by the SDK from `ANTHROPIC_API_KEY`. This file never reads, logs or stores it. Errors carry
     only the class and HTTP status (SI-2).
2. **Price table:** add `claude-haiku-4-5-20251001` with input, output, cache-read and cache-write prices read from
   the live pricing page at entry time, a `source` URL and a read date, plus a `capabilities` row:
   `temperature: true`, `json_schema: true`.
3. **Cross-provider frame test (Phase 3 gate item):**
   - one recall produces one `frame_id`;
   - it is rendered for both providers (`render(frame, openai_profile)` and `render(frame, anthropic_profile)`,
     D-0025 §7);
   - **the memory section bytes are identical** (asserted), and only the provider envelope differs;
   - both calls are recorded as `result` events that name the same `frame_id` in their request;
   - the offline replay of both reproduces both responses with zero network calls;
   - **plus one live smoke run by the owner:** two calls, well under $0.01.
4. **Transplant evidence (reported, not gated, in Phase 3):**
   - EXP-0004's N arm on the test split, re-run with the transfer seat on `claude-haiku-4-5-20251001`, after the
     gated OpenAI run;
   - it uses the same frames, read from the recorded run's frame ids (replayed recall), so only the reasoning model
     changes;
   - the SPEC's transplant-retention metric is a Phase 5 headline; this is its first data point.

## Why this one
- It keeps the owner's pinning rule intact (option a).
- The adapter refuses rather than reshapes requests, so a recording always says exactly what was sent.
- The frame test proves the reasoning boundary mechanically: the same bytes reach both providers.

## Consequences
- New `models/anthropic_messages_provider.py` plus tests (recorded fixtures; the fake SDK client pattern used for
  OpenAI).
- A new `capabilities` field in `prices.json` (D-0021 amendment).
- `call_model` policy: Anthropic is allow-listed per scope like OpenAI (default-deny, SI-3).

## How we'd know it was wrong
- Haiku 4.5's dated ID is retired, or the owner wants a stronger Claude seat: revisit option (b).
- Render equality forces provider-specific formatting into the memory section.

## Questions for the owner
1. **Pinning:** (a) `claude-haiku-4-5-20251001` only (recommended); or (b) amend the rule to accept immutable
   undated snapshot IDs, verified by the Models API, with `created_at` recorded.
2. Confirm: no server-side refusal fallbacks (they would change the model inside one call).
3. Approve the `anthropic` SDK as an optional extra (exact pin).
4. Approve the transplant run (step 4) as reported, not gated, in Phase 3.
