# Phase 3 plan: recall and interface (APPROVED by the owner 2026-10-01, with changes)

**Owner changes at approval (binding; recorded in each ADR):**
- D-0024: cache entries are tagged by scope and key, and served only after grants are confirmed in the snapshot
  transaction; the every-pair cross-scope suite runs through the cache; erasure and grant revocation invalidate by the
  next recall; embedder onnxruntime + tokenizers, pinned by hash, full re-index on model change.
- D-0026: delegations are explicit, recorded, time-limited, revocable and scoped.
- D-0027: no opt-out for unscannable binaries (a future D3).
- D-0028: option (a), Haiku dated snapshot only.
- EXP-0004: per-category floors (≥ 0.70 and ≥ naive in every category); a fixed stale-fact ceiling.
- H4: credential-slot catch ≥ 90%, with FP ≤ 2% on the holdout.


- **Date:** 2026-10-01. Phase 2 is complete (tag `phase-2-complete`).
- **Proposed ADRs:**
  - D-0024: embeddings and search (D3);
  - D-0025: recall pipeline, ContextFrame and trace (D3 / D2);
  - D-0026: interface and principal auth (D3);
  - D-0027: binary attachment scanning (D3);
  - D-0028: Anthropic adapter and cross-provider frame (D2, plus a pin-rule question).
- **Pre-registration draft:** EXP-0004 (recall under interference).
- **New assumptions:** A-0032 … A-0043.
- **Evidence gathered:** A-0032 (encrypted index feasibility and latency, measured 2026-10-01).

## Summary of the central decision (D-0024)
- **No plaintext vectors in Postgres.** Postgres keeps every write in WAL and backups, so key destruction could
  never reach plaintext vectors.
- **Each memory version gets one encrypted index entry:** embedding, nucleus and addresses, under a subkey of
  **the version's own contributor-set key** (D-0023). Erasure then reaches the index with no new path.
- **At query time:**
  - the scope's entries are decrypted into a per-process cache;
  - exact search runs in memory;
  - the cache checks an org `shred_epoch` inside every recall's snapshot, so an erasure is honoured by the very next
    recall, in every process.
- **Measured** (Docker Desktop, single connection; the production setup is re-measured at the gate):

  | Pool size | Warm search | Cold load |
  |---|---|---|
  | 10k | 0.1 ms | 0.12–0.15 s |
  | 100k | 2 ms | 1.2–1.5 s |

  Query embedding takes 3.4 ms on CPU.

## Phase 3 gate (proposed; each item has a named test or measurement)
| # | Item | Source | How checked |
|---|---|---|---|
| 1 | No plaintext content or embedding in any Postgres table, dump or WAL written during the test | D-0024 test 1 | byte scan of tables, `pg_dump`, WAL segments |
| 2 | Erasure (person, period, scope) **and grant revocation** reach index entries and **warm caches** by the next recall, including across processes; the **every-pair cross-scope suite passes through the cache** (warm and cold) | D-0024 tests 2–3b and owner decision 2; D-0023 §3 binding | gate tests with a positive control |
| 3 | Recall eligibility: superseded, lost and unpromoted are never in a frame; contested is shown as contested | D-0025 §2 | unit and property tests |
| 4 | Every frame has a committed ContextAssembled trace before it is returned; trace parts are under contributor-set keys; the header holds no content | D-0025 §8; D-0023 §3 binding | tests, plus a byte scan of header events |
| 5 | Replay of every trace reproduces its `frame_id` (or `unverifiable` after an erasure) | D-0025 §8; A-0036 | replay over all EXP-0004 frames |
| 6 | Cross-stream snapshot consistency under concurrent appends | D-0025 §1; A-0037 | concurrency test |
| 7 | **Latency:** warm p95 ≤ 150 ms end to end and ≤ 50 ms read side, cold ≤ 1 s, at pool 10k; 100k reported | D-0025 §9 | production setup (`core.db.open_pool`), evidence file |
| 8 | **EXP-0004 passes its pre-registered bar** (N ≥ 0.80; N − V ≥ 15 pp, and ≥ 5 pp per set; N − C ≥ 40 pp; N ≥ 0.70 and N ≥ V in every category; stale ≤ 0.05 of T3 and ≤ ½ V; asks ≥ 0.80; safety 0; no reruns) | EXP-0004 | live run by the owner; recorded replay with 0 network |
| 9 | Interface claims: over-claims rejected; valid claims written `verified`; no agent can plant an authoritative correction | D-0026 tests 1, 3 | tests |
| 10 | Tokens never stored or logged; typo checksum; expiry and revocation; no admin or erasure tools on MCP; HTTP without TLS refuses non-loopback | D-0026 tests 2, 4, 5 | tests, plus scans |
| 11 | **Binary attachment scanning:** each carrier rejected on a secret; clean files stored `binary-scanned`; bomb guards; unscannable always rejected (no opt-out); I1 OCR recall ≥ 95% at ≥ 12 px | D-0027; owner item 2026-09-30 | tests; sealed I1 measurement |
| 12 | **Credential-slot target (owner item 2026-09-30):** before real agent data flows through the interface, a fresh **H4** holdout is sealed under the H3 protocol (separate session, frozen before measuring). **Target (owner, fixed): ≥ 90% of credential-slot values caught** (H3: 54–72%), **with false positives ≤ 2% on the H4 holdout**. Loosening detection is not a lever; only additive rules (D-0011) | CURRENT.md gate item; D-0011 | H4 measurement evidence; owner sets the final number before H4 is built |
| 13 | **Cross-provider frame:** one `frame_id` rendered for OpenAI and Anthropic with byte-identical memory sections; both recorded and replayable offline; a live smoke run | D-0028 §3 | test, plus an owner smoke run |
| 14 | Embedder pinned: hash mismatch refuses to start; ONNX equivalence vs reference vectors (if D-0024 (ii)) | D-0024 test 5 | test |
| 15 | Index rebuild is byte-identical and switches atomically | D-0024 test 4 | test |
| 16 | Structure: every new file registered in INDEX with its header; no SDK import outside adapters; no plaintext-index DDL (structure check rejects `vector`, `tsvector` and trigram index DDL in migrations) | constitution; D-0024 | `check_structure.py` (extended) |

**Reported, not gated:**
- the transplant data point (EXP-0004 N arm with a Claude transfer seat, D-0028 §4);
- 100k latency;
- the coverage distribution.

## Build order (after approval)
1. **Freeze first (no recall code before this):**
   - commit the EXP-0004 pre-registration;
   - a separate session builds the dev and test splits, frozen with sha256s;
   - a separate session builds H4 and I1, sealed.
2. **Interface foundation:** `auth.*` migration, authenticate, check_claims (D-0026), the admin CLI. Gate items
   9–10.
3. **Binary scanning** (D-0027). Gate item 11. **Then H4** (gate item 12): real data may not flow before both.
4. **Index:**
   - the `recall.index_entries` and `shred_epoch` migration;
   - the embedder, with pins and equivalence;
   - index_version on the write path;
   - the cache with erasure checks;
   - rebuild.

   Gate items 1, 2, 14, 15.
5. **Recall pipeline** (D-0025): snapshot, merge, narrow, rank, assemble, coverage, trace, replay. Gate items 3–7.
6. **Render, the MCP server, the SDK.** The Anthropic adapter. Gate item 13.
7. **EXP-0004:**
   - dev split (τ frozen, A-0034 check);
   - dry run plus recorded replay;
   - the **owner's live run** (gate item 8);
   - the audit;
   - then the transplant run (reported).
8. Full Phase 3 gate run; tag `phase-3-complete`.

## Owner decisions needed (collected from the ADRs)

**D-0024**
1. **(D3)** The encrypted side index, a decrypted per-process cache with a `shred_epoch` check, no pgvector (SPEC
   stack amended).
2. **(D3)** Accept the residual risk of plaintext in process memory, under the deployment rule: no swap, no core
   dumps.
3. **(D2)** Dependencies: `numpy`, plus `onnxruntime` + `tokenizers` (recommended), or `sentence-transformers` +
   `torch`.
4. Local-only embeddings in Phase 3.
5. Exact search with no ANN until A-0032 breaks.

**D-0025**
1. **(D3)** The eligibility rule, and the trace split (header without content; parts under contributor-set keys;
   the query under the requester's key; committed before the frame is returned).
2. No cross-scope shadowing in Phase 3.
3. The latency targets.
4. τ fixed on the dev split.
5. `addresses[]` on capture (a D-0018 amendment).

**D-0026**
1. **(D3)** Per-principal tokens, source ceilings, and rejecting over-claims.
2. **(D3)** Delegations for `on_behalf_of`.
3. `require_verified` as the default for interface scopes.
4. stdio plus loopback-only HTTP; OAuth later.
5. The `mcp` dependency.

**D-0027**
1. **(D3)** Reject unscannable binaries by default, with a per-scope opt-in.
2. The extraction limits.
3. The OCR engine.
4. I1 and its target.

**D-0028**
1. **Pinning:** (a) `claude-haiku-4-5-20251001`, or (b) amend the rule to accept immutable undated IDs.
2. No server-side refusal fallbacks.
3. The `anthropic` dependency.
4. Transplant reported, not gated.

**EXP-0004:** the bar values, the set design, the arms (especially V's definition), and k = 3.

**Gate item 12:** the H4 credential-slot target number (proposed ≥ 90%).

## Not in Phase 3 (recorded so nothing is silently dropped)
- Threat store and predictor checks: the frame reserves the fields.
- Cross-scope promotion and shadowing.
- An ANN index.
- API embedders.
- OAuth and remote HTTP.
- Webhook signature verification for CI, review and git connectors: Phase 5 coding track.
- Automatic re-derivation of lost beliefs: Phase 4.
- The D-0022 route for bodies over 1 MiB.
- Carried from Phase 2:
  - D-0019 stakes keyword rules;
  - D-0020 corrections → contradictions;
  - events *about* a person written by others (D-0023 open question).
