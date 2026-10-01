# D-0021: Model-provider interface

- **Status:** proposed (D2 parts). **D3 part APPROVED by the owner 2026-10-01: default-deny per-org provider policy.**
- **Tier:** D2 (public interface, new dependencies). The provider policy (which content may leave for which
  provider) is **D3** (privacy boundary).
- **Date:** 2026-10-01
- **Relies on assumptions:** A-0024, A-0025

## Context
Phase 2 is the first phase that calls models (the sleep pass). SPEC requires model independence: any model
reasons, and memory never depends on one.

MNEXA's `model_adapter.py` is single-turn, prompt → `Generation(text, input_tokens, output_tokens, response_id)`:
- it has no system prompt and no decoding parameters, so provider defaults silently apply;
- it has no retries of its own;
- it records nothing about the request.

That is not enough for recorded replay, where a request must be canonical and hashable. It is also not enough for
MNEXA's own compute-parity rule (ADR-0003), which requires the decoding configuration to be frozen.

## Options considered
1. **Port MNEXA's adapter as it is.**
   - Pros: parity of the call shape.
   - Cons: decoding is implicit; requests cannot be hashed.
2. **Own small request/response types plus thin adapters (recommended).**
3. **A third-party router library** (for example LiteLLM).
   - Pros: many providers at once.
   - Cons: a large dependency surface on the path of every prompt, which carries user content. It has its own
     telemetry and retry behaviour that we would have to audit and pin. We only need two providers.

## Decision (proposed)
Option 2.

### Types (`core/model_provider.py`, tiny)
- **`ModelRequest`:** `provider`, `model`, `system?`, `messages: [{role, content}]`, `params: {temperature?,
  top_p?, max_tokens, seed?, response_format?}`, `purpose` (seat name).
  - Unset parameters are explicitly `null`, meaning provider default, and that is recorded. MNEXA parity uses
    `null`.
- **`ModelResponse`:** `text`, `finish_reason`, `usage: {input_tokens, output_tokens, cached_input_tokens?}`,
  `response_id`, `model_reported`, `latency_ms`.
- **`ModelProvider` Protocol:** `complete(request) -> ModelResponse`.

### Canonical request
Deterministic CBOR (D-0008) of the request, **excluding** nothing that reaches the provider.
`request_sha256` = SHA-256 of that encoding. It is the replay key (D-0022).

### Implementations
- **`models/openai_responses_provider.py`** and **`models/anthropic_messages_provider.py`:**
  - The SDKs are optional extras `nacre[openai]` and `nacre[anthropic]`, with exact pins.
  - The SDKs' own retries are disabled.
- **`models/recorded_provider.py`:** replay only; it never touches the network.

### Single entry point
`models/call_model.py` `call_model(session, provider, request)`:
1. **policy check** (below);
2. **canonicalise and hash**;
3. **call**, with bounded retries (each attempt is recorded);
4. **record** a `result` event (D-0022);
5. **return**.

No other file may import a provider SDK. This is checked by `check_structure.py` as a new rule.

### Keys
Provider API keys come from the environment only. They never appear in the ledger, config events, logs or
exceptions.

### Provider policy (D3)
- **Default deny.** Content from a scope may be sent to a provider or model only if the org's
  `config_event` op `model_policy` allows that provider and model.
- The policy is set by an org admin and recorded, like grants.
- Tests use `RecordedProvider`, which needs no policy.

## Why this one
- It is the smallest surface that makes calls hashable, replayable, metered and policy-checked.
- Adapters stay thin, so adding a provider is one file.

## Consequences
- **New dependencies:** `openai` and `anthropic` SDKs, optional and pinned. Needs D-0006-style approval.
- **Parity:** L3 uses the OpenAI adapter with `gpt-4o-mini` and all parameters `null`, like MNEXA.
- **Default models:** other defaults (outside parity) are chosen per seat in config, not in code.

## How we'd know it was wrong
- A provider feature needed later (tools, streaming, images) does not fit `ModelRequest`. The interface would then be
  extended by a superseding ADR.

## Security invariants
Each invariant is checked by the test named after the arrow.

- **SI-1:** a request can only be built inside a scoped session, from events readable in it →
  `test_call_model_rejects_content_outside_session`.
- **SI-2:** provider keys never reach any event or log → the ledger is scanned after a recorded run, plus a unit
  test with a canary key.
- **SI-3:** default-deny provider policy → `test_no_policy_no_call`.
- **SI-4:** no SDK import outside the adapters → a `check_structure.py` rule.

## Questions for the owner
1. Approve the two optional SDK dependencies, pinned?
2. **(D3)** Approve default-deny per-org provider policy?
