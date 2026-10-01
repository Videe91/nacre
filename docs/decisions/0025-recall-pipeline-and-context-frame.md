# D-0025: The recall pipeline, the frozen ContextFrame, and the ContextAssembled trace

- **Status:** proposed (2026-10-01). Awaiting owner approval. No code until accepted.
- **Tier:** D3 for §1 (what a recall may see), §6 (the reasoning boundary) and §7 (traces of content); D2 for the
  rest (cross-module behaviour, the persistence format of frames and traces, the latency target).
- **Date:** 2026-10-01
- **Relies on assumptions:** A-0032, A-0034, A-0036, A-0037, A-0038 (new); A-0004, A-0007, A-0030

## Context
- **SPEC "Recall and the reasoning boundary"** defines eight steps:
  1. freeze the snapshot;
  2. merge scopes;
  3. identity narrows;
  4. similarity ranks;
  5. safe assembly with quorum pruning;
  6. threat and predictor checks;
  7. gap awareness;
  8. trace.

  Memory produces an immutable, hashed ContextFrame, and any model reasons over it.
- **Phase 2 transfer** used every recall-eligible head of a single isolated scope. With many competing memories
  per scope (EXP-0004), that fails by design. This ADR defines how the context is chosen.
- **Embeddings and search:** D-0024. Erasure: D-0023. Eligibility (lost heads excluded): D-0023 §5.

## Decision (proposed)

### 1. The request, and freezing the snapshot
- **Request:** `recall(principal, scopes, query, addresses?, budget)`.
  - `principal` is authenticated by the interface (D-0026).
  - `scopes` are named levels: task, project, user, team. Each resolves to a scope id the principal holds a grant
    on; RLS enforces this (D-0005).
  - `addresses` are optional identity addresses (§3).
  - `budget` is the maximum number of items and the maximum rendered characters.
- **Freeze:** one `REPEATABLE READ, READ ONLY` transaction holds the whole recall. In it, the snapshot is read once.
  - **Cross-stream snapshot = a set of per-stream positions:** `{stream_id: commit_seq}` for every stream of every
    merged scope. It is the highest committed `commit_seq` visible in the transaction. A Postgres snapshot is one
    consistent cut across tables, so the vector is consistent across streams by construction. This is the
    "watermark vector" Phase 1 deferred (single-stream AS_OF(N) generalised).
  - Also read once: `shred_epoch` (D-0024 §3), the active projection generation, and the active index generation.
- **Every later step reads only through this snapshot.** Recall is read-only, so nothing is written until §8.

### 2. Scope merge
- **Candidate pool:** the union of **recall-eligible heads** over the merged scopes.
- **Recall-eligible (binding):** at the snapshot, the head is `active`, `contested` or `fallback`, or it is an
  `episode`, AND its content is readable (not lost, D-0023 §5).
  - `superseded` heads and unpromoted proposals are never eligible. Superseded content is the stale-fact error
    EXP-0004 measures.
- **"Narrowest wins":** each candidate carries its scope level. On any tie in §4, or any cut in §5, the narrower
  scope ranks first.
- **Cross-scope shadowing is not part of Phase 3.** Hiding a wider-scope belief because of a narrower one needs the
  cross-scope promotion decision, which is open in the SPEC. Both appear, labelled by scope. Owner question 2.

### 3. Identity narrows
- **Addresses:** typed strings, `{system, code, entity, file, cluster, domain}`. Example: `code:src/payments/retry.py`,
  `system:payments`.
- **Where memories get addresses (D-0018 amendment, D2):**
  - capture events may carry `addresses[]` in the payload. They are content, so they are encrypted with the event;
  - a version's addresses are the union of its source events' addresses (deterministic);
  - they are stored in the index entry (D-0024 §1).
- **Narrowing:**
  - if the request has addresses, keep candidates that match all of them (conjunctive);
  - if fewer than `min_pool` (16) remain, relax one level at a time, `code → system → cluster → domain → global`,
    and record each relaxation in the frame;
  - with no request addresses, the pool is the whole merged pool;
  - an address never comes from the query text by model inference: it is the caller's explicit input, or none.

### 4. Similarity ranks (three channels, deterministic fusion)
- **Channels per candidate:**
  - **semantic:** the quantised cosine of the query embedding against the nucleus embedding (D-0024 §4);
  - **lexical:** BM25 (k1 = 1.2, b = 0.75) of the query tokens over the nucleus tokens;
  - **entity:** the count of the request's addresses the candidate matches exactly, before relaxation.
- **Tokens:** Unicode NFKC, casefold, split on non-alphanumerics, no stemming. Identical in the index and the query.
- **Abstention:** a channel abstains when it cannot discriminate in this pool:
  - semantic: max − median < 0.05;
  - lexical: no candidate scores above 0;
  - entity: no request addresses, or all candidates tie.

  Abstentions are recorded in the frame.
- **Fusion:** reciprocal-rank fusion over the non-abstaining channels, `Σ 1/(60 + rank)`, computed in exact
  rational arithmetic and then quantised. There are no learned weights; tuning waits for Phase 4.
- **Tie order:** fused score, then narrower scope, then higher `commit_seq` of the version, then `version_event_id`.

### 5. Safe assembly (quorum pruning, breadth by default)
- **Pruning:** a candidate may be pruned **only if at least two active channels** place it outside their own top-M
  (M = 3 × budget items). With fewer than two active channels, nothing is pruned by relevance; only the budget cuts
  (in fused order).
- **Fill:** take candidates in fused order until the item or character budget is reached.
- **Pinned always, whatever the budget:**
  - a `contested` head is shown with its status;
  - if a candidate belief is contested, its pending contradiction context (the targeted version) comes with it.
- **Threat and predictor checks (SPEC step 6):** the threat store and the predictor are not built (later phases).
  The frame reserves `threats: []` and `prediction: null`. The step is a recorded no-op in Phase 3.

### 6. Gap awareness
- **`coverage` is one of `strong | weak | none`**, deterministic:
  - **none:** the pool is empty even after relaxing to global, OR every channel abstains or has no hit;
  - **strong:** the top item is in the top 3 of at least two active channels, AND its semantic score ≥ τ_strong;
  - **weak:** otherwise.
- **The thresholds τ** are fixed on the EXP-0004 **dev split**, recorded as a `config_event`, and frozen before the
  blind test run (EXP-0004).
- **The frame carries `coverage` and a fixed instruction** for the renderer: on `weak` or `none`, the agent should
  ask rather than guess. Asking vs guessing is measured in EXP-0004.

### 7. The frozen ContextFrame (the reasoning boundary)
- **Contents:** a deterministic CBOR map (D-0008 subset: no floats; scores are integers):
  - `v`, `frame_kind`, `snapshot` (the §1 vector), `scopes`, `principal_id`, `query_sha256`, `addresses`,
    `relaxations`;
  - `embedder_id`, `index_generation`, `projection_generation`, `pipeline_version`;
  - `channels` (with abstentions), `coverage`;
  - `items[]`. Each item has:
    - `version_event_id`, `object_id`, `kind`, `status`, `scope_level`;
    - `text` (nucleus or fallback text), `qualifiers`, `origin`;
    - `scores {semantic, lexical, entity, fused}`, `ancestry` (ledger event ids).
- **`frame_id` = sha256 of the canonical bytes.** The frame is immutable: a recall returns `(frame_id, frame)`.
- **Rendering for a model** is a **pure function** `render(frame, provider_profile) → messages`. It lives in the
  interface layer:
  - it may format, but never add, drop or reorder items;
  - the rendered memory section is byte-identical across providers;
  - **reasoning prompts never flow back into the frame.**

### 8. The ContextAssembled trace (atomic, erasable)
- **When:** the moment a frame is handed to a caller, its trace is appended, in one transaction, as
  `memory_event` op `context_assembled`. Recall itself stays read-only: the snapshot transaction is separate and
  committed first.
  - The trace commits **before** the frame is returned. If the trace append fails, the recall fails; no untraced
    frame leaves the service.
- **The trace is split so that D-0023 holds and the 256 cap never blocks recall:**
  - **header event, under the system key, with no content:**
    - `frame_id`, `snapshot`, `principal_id`, `coverage`;
    - the item `version_event_id`s and `content_mac`s (structural, as in `interp`);
    - the generation and embedder ids, `query_sha256`;
    - the ids of the part events;
  - **content part events:** the item texts. Grouped so each part's sources ≤ 256 contributors; under contributor-set
    keys (sources = the item versions);
  - **query part:** the query text and addresses. sources = the requesting principal's subject (the person when
    `on_behalf_of` names one, D-0023 §6; else the stream's system subject).
- **Erasing a contributor makes the matching part unreadable.** The header stays: ids and MACs only, as in `interp`.
- **Replay (gate item):**
  - re-running the pipeline at a trace's `snapshot` with the same generations and embedder must reproduce the same
    `frame_id`;
  - if any item's key has been destroyed since, replay reports `unverifiable`, never a mismatch (as rebuild does,
    D-0017 amendment 2).

### 9. Latency target (proposed; the SPEC's open "recall latency target")
- **Measured end to end in the service:** request in, trace committed, frame out. Production setup: pooled
  `core.db.open_pool`, same machine. A merged pool of 10k entries (gated) and 100k (reported).

| Path | Target (p95) | Basis |
|---|---|---|
| **Warm recall, pool ≤ 10k** | **≤ 150 ms** | 3–4 ms embed + ~0.1 ms search + O(pool) lexical and identity + snapshot reads + 2–3 appends (A-0007: p99 < 50 ms at ≤ 4 writers) |
| Warm recall, pipeline only (no trace commit) | ≤ 50 ms | the read side, for interactive agents |
| **Cold first recall in a process, pool ≤ 10k** | **≤ 1 s** | measured fetch + decrypt 0.12–0.15 s (Docker Desktop) |
| Pool 100k (warm / cold) | reported, not gated | 2 ms search; 1.2–1.5 s cold fetch on Docker Desktop |

- **If the warm target fails:** the remedy order is an in-memory inverted index for lexical, then group commit for
  trace appends (A-0019), then Rust (SPEC). It is never a plaintext index.

## Options considered (per open choice)
1. **Snapshot:**
   - a vector of per-stream positions read in one RR transaction (**chosen**);
   - a global commit counter: rejected, it would serialise appends across streams;
   - a wall-clock AS_OF: rejected, not reproducible.
2. **Fusion:**
   - RRF (**chosen**: rank-based, no tuning, robust to scale differences);
   - a weighted score sum: rejected, it needs weights before evidence exists (Phase 4 tuner);
   - a model re-ranker: rejected, it is non-deterministic, an injection surface, and its reasoning would leak into
     the frame.
3. **Pruning:**
   - quorum of two channels (**chosen**, SPEC);
   - top-k by fused score only: rejected, one confident but wrong channel would prune true breadth;
   - no pruning: rejected, budgets then cut arbitrarily.
4. **Trace content:**
   - split header and parts under contributor-set keys (**chosen**);
   - one trace event under the union key: rejected, it fails closed past 256 and would block recall;
   - hashes only: rejected, the SPEC requires replaying "exactly what the model saw", and hashes alone cannot show it
     after the fact.
5. **Gap awareness:**
   - deterministic thresholds from a dev split (**chosen**);
   - model-judged sufficiency: rejected, the same reasons as for re-rankers.

## Consequences
- **New files (planned, in INDEX):**
  - `recall/freeze_snapshot.py`, `recall/merge_scopes.py`, `recall/narrow_by_identity.py`;
  - `recall/rank_candidates.py`, `recall/assemble_frame.py`, `recall/assess_coverage.py`;
  - `recall/record_context_assembled.py`, `recall/recall_context.py` (the public entry that orchestrates them);
  - `recall/replay_frame.py`.
- **Amendments:** D-0018 (capture `addresses[]`); D-0017 (version content carries `addresses`).
- **No product change for the Phase 2 transfer path.** EXP-0003's frozen instrument is untouched; EXP-0004 uses the
  new recall.

## How we'd know it was wrong
- EXP-0004 dev split: the right memory is often not in the frame (a recall problem), or it is in the frame but buried
  (an assembly problem).
- The asks-vs-guesses metric is poor at the frozen τ.
- Replay produces a `frame_id` mismatch (A-0036, determinism).
- The latency target is missed on the production setup.

## Questions for the owner
1. **(D3)** Approve the eligibility rule (§2) and the trace split (§8): header without content under the system
   key; content parts under contributor-set keys; the query under the requester's key; the trace committed before
   the frame is returned.
2. Confirm: no cross-scope shadowing in Phase 3 (both shown, labelled), pending the cross-scope promotion ADR.
3. Approve the latency targets (§9): warm p95 ≤ 150 ms end to end and ≤ 50 ms read side; cold ≤ 1 s at 10k; 100k
   reported.
4. Approve fixing τ (coverage) on the EXP-0004 dev split before the blind run.
5. Approve `addresses[]` on capture (a D-0018 amendment), supplied by the caller and never inferred by a model.
