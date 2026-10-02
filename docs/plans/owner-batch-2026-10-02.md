# Owner batch, 2026-10-02: interface gaps, billing edge cases, address edge cases

One decision per row. Each row gives the recommendation first, with the alternatives and why. **Nothing here blocks
the τ dev run or EXP-0004.** Sources:
- D-0026 amendment 3 (proposed);
- D-0021 amendment 2 and D-0022 amendment 2 (built);
- D-0018 amendment 2 (built);
- `docs/state/CURRENT.md`.

## A. Interface: D-0026 amendment 3 (proposed; none of these is built except A4's defaults)

| # | Gap | Recommended | Alternatives (and why not) |
|---|---|---|---|
| A1 | **`get_frame(frame_id)` has nothing to look up.** Frames are not stored, the trace is encrypted, and `replay_frame` returns verdicts, not the frame. | `recall_context` also returns `trace_ref` (`<stream>:<commit_seq>`), and the tool becomes `get_frame(trace_ref, frame_id)`. It replays as the caller and returns the rebuilt frame only when every item verifies and the rebuilt `frame_id` matches; otherwise `frame_unavailable` with verdict counts and no content. `replay_frame` gains a return of the rebuilt body. No new storage. | **A plaintext `frame_id → trace` index:** a new table, and it reveals that two recalls produced the same frame. **Drop `get_frame` in Phase 3:** D-0026 §4 names it, and EXP-0004 replay benefits from it. |
| A2 | **`record_statement` has no capture entry.** D-0018 has no statement payload. | Drop it from the Phase 3 MCP surface. A person's statement is a `correction` or a chat `message`. | Define a payload in a D-0018 amendment: a new persistence format with no consumer yet. |
| A3 | **Where `require_verified` lives** (your decision 3 approved it as the default for interface scopes). | Add a migration column `scopes.scopes.require_verified boolean NOT NULL DEFAULT false`. Scopes created through the interface or admin CLI set it true. `append_event` rejects a write without matching verified claims in such a scope (`unverified_write`), inside the write transaction. Existing scopes stay false. | **Enforce only in the MCP server:** any other path to `append_event` would bypass it. |
| A4 | **Rate-limit values.** | Keep what is built: an in-process token bucket per principal, 120 calls a minute, bursts of 30; values in config, and a change is a config_event. | **A Postgres-backed limit shared across processes:** Phase 3 runs one server process per machine. |

## B. Billing (D-0021 amendment 2 is built; these are the cases your rule left open)

| # | Case | Today (built) | Recommended | Why |
|---|---|---|---|---|
| B1 | **A connection dropped after the request was fully sent.** It cannot be told apart from one that never connected. | Recorded as not billed ($0). | **The worst case.** | The cap must never under-count. Over-counting can only stop a run early. |
| B2 | **HTTP 429 / 5xx / 529.** | Not billed ($0). | **Keep it, and record it as an assumption** (providers do not bill rejected or errored requests), with a check against the provider's usage console after the live run. | Charging them as the worst case would make rate-limit storms abort runs for no real cost. |
| B3 | **Truncation differs by adapter.** Anthropic raises a billed `truncated` error (D-0028). OpenAI returns the truncated reply as a normal, costed response. | Asymmetric. | **Keep it.** | Changing OpenAI would change how the EXP-0004 sleep pass and transfer behave on truncation, and both are pre-registered on OpenAI. Cost is recorded correctly either way. |
| B4 | **Cache-write tokens** (Anthropic only; Nacre never sends `cache_control`). | Fail closed as `unpriced_usage`. The worst case uses the plain input price. | **Use the highest input-side price (the 1-hour cache write) for the worst case's input part.** | It cannot under-count; the case should never occur. |

## C. Addresses on capture (D-0018 amendment 2 is built; open edge cases)

| # | Case | Today (built) | Recommended | Why |
|---|---|---|---|---|
| C1 | **Secret stripping can rewrite an address.** For example `code:src/aws/<a key-shaped name>.py` is stored as `code:[REDACTED:…]`; it then never matches, and the redaction is listed. | Stored redacted. | **Refuse the capture** (`invalid_request: an address contains secret-shaped text`), so the caller learns at once. | **Exempting addresses from stripping** weakens D3 secret handling (no). **Leaving it silent** gives an address that never matches, with no signal. |
| C2 | **Contested and superseded versions keep the head's addresses** (they copy the head content). | As described. | **Keep it.** The contradiction decisions' addresses describe the counter-evidence, not the belief. | Adding them would let a contradiction widen where a stale belief is found. |
| C3 | **Capture is strict about form, recall is lenient.** Capture refuses `Code:x` or `code: x`; recall requests are normalised. | Asymmetric. | **Keep it.** Stored data stays canonical, and a caller's typo in a query still finds the right memory. | Normalising at capture rewrites caller data silently (the rule is "refuse, never rewrite"). |
| C4 | **`check_addresses` lives in `capture/record_decision.py`** (shared by the five capture files). | As described. | **Move it to its own file, `capture/check_addresses.py`** (one functionality per file), registered in INDEX. | Housekeeping; D1. Listed only for visibility. |

## D. Sleep (D-0020 amendment 1 is built; open)

| # | Case | Recommended | Why |
|---|---|---|---|
| D1 | **The action id on lesson proposals.** Today only the episode's members hold the action. | **Leave it.** It can be re-derived from the outcome's `outcome_for`. | Adding it to the proposal's D-0023 `sources` would change its key. |
| D2 | **No persisted marker for unlinked action outcomes.** They are counted again on every run. | **Leave it.** The count is a report, and a marker would be a new persisted op. | Revisit if production shows many unlinked outcomes. |
