# D-0024: Embeddings and search: an encrypted recall index, decrypted in memory

- **Status:** accepted (owner, 2026-10-01) with the decisions below
- **Tier:** D3. It sets a privacy boundary: where content-derived vectors may exist, and how erasure reaches them.
- **Date:** 2026-10-01
- **Relies on assumptions:** A-0032, A-0033, A-0034, A-0035 (new); A-0008, A-0030, A-0031

## Owner decisions at acceptance (2026-10-01)
1. **Approved (D3):** the encrypted side index and the decrypted per-process cache; no pgvector.
2. **The cache is outside Postgres RLS, so it enforces scope itself (binding):**
   - every cache entry is tagged with its `scope_id` and `key_id`;
   - an entry is served **only after the caller's grants on that scope are confirmed in the same transaction as the
     snapshot** (D-0025 §1). No grant check means nothing is served from the cache;
   - the **full every-pair cross-scope suite** (the D-0005 isolation suite: every ordered pair of scopes, including
     sibling, parent/child and cross-org) is run against **recall through the cache**, warm and cold;
   - **erasure and grant revocation both invalidate cached entries by the next recall.** Revocation is covered by
     re-checking grants inside every recall's snapshot transaction, so a revoked grant serves nothing from the next
     recall on. Erasure is covered by `shred_epoch` (§3).
3. **Accepted:** the residual risk of plaintext in process memory. Deployment rule: swap and core dumps off for the
   service.
4. **Embedder runtime: `onnxruntime` + `tokenizers`** (option (ii)).
   - The model file and the tokenizer file are pinned by sha256.
   - Every index entry carries the embedder version (`embedder_id`).
   - **A model change triggers a full re-index** into a new index generation, switched atomically. Entries from
     different embedders are never mixed in one search.
   - `numpy` is approved as the vector-math dependency.
5. **Approved (implied by plan approval):**
   - local-only embeddings in Phase 3 (an API embedder needs its own ADR under D-0021);
   - exact search with no ANN until A-0032 breaks.

## Context
- **What recall needs:** a semantic channel (SPEC "Similarity ranks"), and so embeddings of memory content.
- **The risk:** embeddings are content-derived and partly invertible (inversion attacks recover much of the source
  text from sentence embeddings).
- **The rule they fall under:** D-0023 §3 makes them derived records: **same rule, binding**. They must be encrypted
  under the contributor-set key of the content they come from, and be erased with it.
- **Why plaintext vectors fail:** the SPEC's stack line says "Postgres + pgvector". But plaintext vectors in Postgres
  are also written to WAL, base backups, replicas and pg_dump output. Destroying a key cannot reach any of those
  copies, so pgvector over plaintext breaks crypto-shredding (D-0004) for every erased person.
- **The same holds for any plaintext lexical index** (`tsvector`, trigram) and for plaintext identity tags.

**Measured (A-0032 evidence, 2026-10-01; single persistent connection, not the pooled production setup):**
- Decrypting about 154 MB of encrypted f32 vectors (100k × 384) costs 20–26 ms; one row per record costs 157 ms.
- Warm exact top-50 search costs 0.1 ms at 10k and 2 ms at 100k.
- Fetching ciphertext from Postgres dominates cold load: 0.12 s at 10k, 1.2 s at 100k (through Docker Desktop).
- Local MiniLM query embedding: 3.4 ms median on CPU.

## Options considered
1. **Plaintext pgvector (the SPEC's original stack line).** Fast, with an ANN index in the database.
   - **Rejected:** WAL, backups and replicas keep invertible vectors after erasure. This violates D-0004, D-0023 and
     D-0017's "no content plaintext in the projection".
2. **pgvector over encrypted or transformed vectors** (random rotation, distance-preserving transforms,
   property-preserving encryption).
   - **Rejected:** a distance-preserving transform is still invertible given known pairs, so it is not encryption.
     Real property-preserving schemes are immature and leak ranks.
3. **Embeddings inside the ledger version event body** (encrypted with the version, so erased with it).
   - **Rejected:** changing the embedding model would mean appending re-embedded copies of every version to the
     append-only ledger. The index is derived and rebuildable; it does not belong in the evidence log.
4. **Encrypted per-scope ANN index blob** (one serialized HNSW per scope, encrypted).
   - **Rejected for now:**
     - one blob mixes many contributor sets, so it would need a key over the union of contributors, which exceeds
       the 256 cap (D-0023) and fails closed;
     - one person's erasure would destroy the whole scope index;
     - and at ≤ 100k per pool, exact search is already 2 ms warm. It can be revisited if A-0032 breaks.
5. **Lexical and identity only, no embeddings.**
   - **Rejected as the only channel:** it misses paraphrased corrections, which is the case EXP-0004 is built to
     test.
   - **Kept as two of the three channels.**
6. **Recommended: an encrypted recall index (side table), decrypted into a per-process cache, exact search in
   memory.** Details below.

## Decision (proposed)
### 1. What is indexed
- **One index entry per interpretation version** (belief, fallback, episode) that is recall-eligible when written.
- **The entry's plaintext is a deterministic CBOR map:**
  - `embedding`: 384 × f32 little-endian bytes, unit-normalised; the embedding of the **nucleus**;
  - `nucleus`: text, for the lexical channel and rendering;
  - `addresses`: identity addresses (D-0025 §3);
  - `kind`.
- **Status is not stored in the entry.** Status changes (active / contested / superseded / lost) are read from the
  `interp` projection at the recall snapshot, never from the cache.

### 2. Where it lives, and under which key
- **Table `recall.index_entries`**, columns:
  - `scope_id`, `index_generation`, `version_event_id`, `key_id`, `embedder_id`, `seq` (a monotonic insert
    order, used for incremental cache refresh);
  - `body`: AES-256-GCM ciphertext, with AAD = (scope, generation, version id, embedder id).
- **No plaintext and no plaintext-derived values** (no MACs, no tokens, no norms) in any column.
- **Key:** a purpose-separated subkey (HKDF, purpose `recall_index`) of **the version event's own data key**. That
  key is already the version's contributor-set key under D-0023, so:
  - erasing any contributor destroys the version and its index entry together;
  - `forget_period` reaches it through source months;
  - `delete_scope` reaches it through the master key.

  No new key-management path is needed.
- **WAL and backups only ever contain ciphertext.** Destroying the key is enough, as it is for the ledger.
- **It is derived and rebuildable.** It is a cache of what the ledger proves, not evidence. Rows may be deleted and
  rebuilt (unlike `ledger.events`).
  - **New index generations:** built offline (embedder change, repair), then switched atomically, the same pattern
    as D-0017 amendment 2.
  - **Grants and RLS:** same scope RLS as `interp`. `nacre_app` inserts and selects; only the rebuild job deletes.

### 3. Query time: a decrypted cache, checked against erasure on every recall
- **Per process, per (scope, index generation):**
  - a decrypted matrix (n × 384 f32), the entries' nucleus and addresses, and each row's `key_id`;
  - loaded on first use (cold);
  - refreshed incrementally by `seq` (only new rows are fetched).
- **Erasure check (the binding rule):**
  - every key destruction (erase_person, forget_period, delete_scope) also increments an org-level `shred_epoch`
    **in the same transaction**;
  - every recall reads `shred_epoch` inside its snapshot transaction (D-0025 §1);
  - if it changed since the cache last checked, the cache asks which of its `key_id`s are destroyed and evicts those
    rows before ranking.
  - **Guarantee:** no recall whose snapshot begins after an erasure commits can return the erased content, from any
    process.
  - Belt and braces: status from the projection at the snapshot also excludes lost heads (D-0023 §5).
- **The cache never touches disk.** Plaintext exists only in process memory, the same exposure Phase 2 already has
  when `read_heads` decrypts. Deployment must disable swap for the service and core dumps (A-0033).
- **Memory cap:** an LRU across scopes, with a configured budget in MB. An evicted scope is simply cold next time.

### 4. Search
- **Exact (brute-force) normalised dot product** over the narrowed candidate pool. No ANN index in Phase 3
  (A-0032).
- **Determinism:** scores are quantised to integers (cosine × 10⁴, rounded half-even) before ranking, and ties are
  broken by `version_event_id`. A frame's ranking therefore does not depend on BLAS summation order beyond 1e-4
  (A-0036).
- **f32 in memory.** Storing f16 halves the fetch but needs an upcast at load. Deferred until the production-setup
  measurement says the fetch matters (A-0035).

### 5. The embedder
- **Default: a local model.** `sentence-transformers/all-MiniLM-L6-v2` (384-d, what MNEXA used):
  - pinned by HF revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`, plus the sha256 of every weight and tokenizer
    file;
  - **no network at runtime**; it loads from a vendored or pre-fetched path, and refuses to start if a hash
    differs.
- **`embedder_id` is recorded on every index entry and every ContextFrame.** It is `name@revision#weights-sha256`.
  This is Nacre's "dated pin" for local models: a revision hash, where a date is not meaningful.
- **Behind an `Embedder` interface** (`core/`, a Protocol). An API embedder (for example OpenAI embeddings) is
  possible later. It would send every memory's nucleus and every query to a provider, so it falls under the D-0021
  policy (allow-list, per-scope consent) and needs its own ADR. **Not in Phase 3.**
- **Dependencies (D2, owner question 3):**
  - `numpy` is needed for vector math (pure Python is about 1000× slower at 100k × 384);
  - to run the model:
    - **(i)** `sentence-transformers` + `torch`: exactly MNEXA's stack, but a heavy install;
    - **(ii)** `onnxruntime` + `tokenizers` with the ONNX export of the same revision: light. It needs a frozen
      equivalence test (cosine ≥ 0.9999 against reference vectors made with (i), committed as data).
  - Recommendation: **(ii)**, with the equivalence test as a gate item.

### 6. Channels without plaintext indexes
- **Lexical:** BM25 over the cached nucleus tokens of the candidate pool, computed in memory per recall. No
  `tsvector` or trigram index exists anywhere.
- **Identity:** address matching in memory over the cached `addresses`. No plaintext tag columns.
- **Cost:** both are O(pool) per recall. At 10k this stays inside the D-0025 latency target (to be measured at
  build; if not, a cached per-scope inverted index in memory is the remedy).

## Why this one
- **Erasure stays a key operation.** Nothing content-derived exists outside ciphertext, so D-0004 and D-0023 hold
  for embeddings with no new erasure path.
- **The measured cost is small:** milliseconds warm, sub-second cold at the sizes Phase 3–5 will see.
- **It keeps all three SPEC channels** without a single plaintext index.
- **Embedder changes are routine:** rebuild into a new generation; the ledger is untouched.

## Consequences
- **SPEC:** the stack line "Postgres + pgvector … similarity search in one database" is superseded. Postgres stores
  the encrypted index; similarity runs in the service process. pgvector is not used.
- **New:**
  - migration `recall.index_entries` plus `keys.shred_epoch`;
  - `recall/index_version.py` (write an entry when a version is written: same transaction as the version, so an
    entry exists if and only if the version does);
  - `recall/load_index_cache.py`;
  - `recall/rebuild_index.py`;
  - `core/embedder.py` (Protocol);
  - `recall/embed_local.py` (the only file that imports the runtime);
  - the `execute_due_shreds` change (bump `shred_epoch`).
- **Cost on the write path:** one embedding per version, about 0.5 ms batched or 3–4 ms single, on CPU.
- **Cold start per process:** about 0.15 s per 10k entries (measured, Docker Desktop).
- **Residual risk:** plaintext in process memory while cached. The same as any process that reads decrypted
  memory; it is mitigated, not eliminated (A-0033).

## Tests (Phase 3 gate items)
1. **No plaintext content anywhere in Postgres.** For a scope with known memory, every column of every
   `recall`/`interp` table, a `pg_dump`, and the WAL segments written during the test (`pg_waldump` / raw bytes)
   contain none of the nucleus strings and none of the embedding byte runs.
2. **Erasure reaches the index and the cache:**
   - erase P, then:
     - every index entry of versions with P as a contributor is undecryptable;
     - the next recall in an **already-warm process** returns none of them;
     - versions without P are still recalled (positive control);
   - the same for forget_period, and for delete_scope.
3. **Concurrency:** an erasure that commits between two recalls in another process is honoured by the second.
3a. **Grant revocation:** after a grant is revoked, the next recall by that principal (in an already-warm process)
    serves nothing from that scope.
3b. **Cross-scope, through the cache:** the every-pair D-0005 isolation suite, run with recall through a warm cache
    and a cold one: no frame ever holds an item from an ungranted scope.
4. **Rebuild:** `rebuild_index` reproduces a generation byte-identically (same embedder) and switches atomically.
5. **Pinned embedder:** a weights or tokenizer hash mismatch refuses to start. With (ii), the equivalence test
   against the frozen reference vectors passes.
6. **Production-setup latency re-measurement** (pooled, `core.db.open_pool`), reported against the D-0025 target.

## How we'd know it was wrong
- Per-pool sizes grow past about 100k, or warm search breaks the latency target (A-0032): add an encrypted ANN per
  contributor-set segment.
- The cold load is too slow on the production setup (A-0035): store f16, or keep a warm cache process per org.
- MiniLM cannot find paraphrased corrections on the EXP-0004 dev split (A-0034): swap the embedder (a new
  generation) before the blind run.

## Questions for the owner
1. **(D3)** Approve the encrypted side index, with a decrypted per-process cache and the `shred_epoch` check before
   every recall, as the only place embeddings exist? This supersedes "pgvector" in the SPEC stack.
2. **(D3)** Accept the residual risk of plaintext in process memory? Deployment rule: no swap for the service, core
   dumps off.
3. **(D2)** Dependencies: `numpy`, and embedder runtime (ii) `onnxruntime` + `tokenizers` (recommended) or
   (i) `sentence-transformers` + `torch`?
4. Confirm local-only embeddings in Phase 3, with API embedders a later ADR under D-0021?
5. Confirm exact search with no ANN until A-0032 breaks?
