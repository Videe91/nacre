# A-0032 evidence: encrypted recall index feasibility and latency (2026-10-01)

- **For:** D-0024 (proposed, D3): embeddings and search.
- **Code:** `scripts/bench_encrypted_vectors.py`, `scripts/bench_minilm_encode.py` (research scripts, not product code).
  Repo at a07cc2a, clean.
- **Machine:** Apple M3 Max, 36 GiB, macOS (Darwin 25.6.0).
- **Postgres:** 17.11 in Docker Desktop (tmpfs), reached over loopback through Docker's port forward.
- **Connection setup, labelled as the testing rules require:** **one persistent psycopg 3.3.6 connection, NOT
  `core.db.open_pool`**. These are not production-setup numbers. The production-setup re-measurement is a Phase 3
  gate item.
- **Vector benchmark software:** Python 3.14.3, numpy 2.5.3 (Accelerate BLAS), cryptography 50.0.2.
  - The benchmark ran in a scratch venv. numpy is **not** a Nacre dependency; adding one is a D2 decision (D-0024 Q4).
- **Synthetic data:** random unit vectors, seed 20261001. Search is exact, so the results do not depend on the data.
  The encrypted path's top-50 was asserted equal to a plaintext brute-force top-50 on every case.

## What was timed
- **Each query:**
  - fetch every encrypted blob of the scope (`bytea`, `STORAGE EXTERNAL`, no compression);
  - AES-256-GCM decrypt each blob;
  - `np.frombuffer` to a matrix;
  - exact dot-product top-k (k = 50).
- **Cold** = all four steps. **Warm** = search only, with the decrypted matrix already cached in process memory.
- **Runs:** cold 15 runs per case; warm 200.
- **R** = vectors per encrypted blob. R = 1 is one encrypted row per memory record.

## Results (median / p95, ms)

| N | R | dtype | stored MB | fetch | decrypt | cold total | warm search |
|---|---|---|---|---|---|---|---|
| 1,000 | 1 | f32 | 1.56 | 13.8 | 1.6 | 15.6 / 20.8 | 0.02 / 0.02 |
| 1,000 | 64 | f32 | 1.54 | 11.9 | 0.3 | 12.3 / 13.5 | 0.02 / 0.02 |
| 1,000 | 64 | f16 | 0.77 | 6.5 | 0.2 | 7.3 / 7.9 | 0.53 / 0.56 |
| 10,000 | 1 | f32 | 15.6 | 128.6 | 16.1 | 146.1 / 149.2 | 0.10 / 0.11 |
| 10,000 | 64 | f32 | 15.4 | 115.1 | 2.8 | 119.2 / 122.7 | 0.10 / 0.13 |
| 10,000 | 64 | f16 | 7.7 | 58.1 | 1.4 | 65.3 / 71.3 | 5.24 / 5.37 |
| 100,000 | 1 | f32 | 156.4 | 1291.2 | 157.1 | 1460.4 / 1544.8 | 1.95 / 2.10 |
| 100,000 | 64 | f32 | 153.6 | 1150.6 | 25.9 | 1181.6 / 1219.2 | 1.96 / 2.21 |
| 100,000 | 1024 | f32 | 153.6 | 1152.1 | 20.5 | 1186.6 / 1195.7 | 2.28 / 2.43 |
| 100,000 | 64 | f16 | 76.8 | 574.9 | 14.2 | 644.4 / 655.6 | 53.0 / 54.4 |
| 100,000 | 1024 | f16 | 76.8 | 571.1 | 10.4 | 636.5 / 648.0 | 52.8 / 53.9 |

**Query embedding** (`sentence-transformers/all-MiniLM-L6-v2`, revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`):
- **Setup:** CPU, 4 threads, offline from the local HF cache; MNEXA's research venv (Python 3.13.5,
  sentence-transformers 6.1.0, torch 2.14.1).
- **One short query:** median **3.38 ms**, p95 4.19 ms (100 runs after 5 warm-ups).
- **Batch:** 1,000 short documents in 0.47 s (batch 64). Dimension 384, float32.

## Reading
1. **Decryption is cheap.** About 6 GB/s with blobs; 157 ms at 100k when every record is its own blob (one AES
   call per record).
2. **Fetching from Postgres dominates cold cost.** About 7.5 ms per MB here (≈ 133 MB/s through Docker Desktop's
   port forward). This is very likely pessimistic for Linux Postgres (A-0035), but it is the number we have.
3. **Warm exact search is negligible at the sizes that matter:** 0.1 ms at 10k and 2 ms at 100k. An approximate
   (ANN) index buys nothing measurable below 100k per merged pool (A-0032).
4. **float16 storage halves the fetch, but numpy has no BLAS for float16.** Searching in f16 is 25× slower. If f16
   storage is used, upcast once at load. Memory is then the same as f32, and only the fetch is saved.
5. **Grouping records per blob (R = 64) cuts decrypt by 6× at 100k, but the total by only about 20%** (fetch
   dominates). Grouping can follow the contributor-set key (D-0023), so no blob ever mixes keys.
6. **Feasible:** an encrypted-at-rest index that is decrypted in memory, with a per-process warm cache, recalls in
   milliseconds once warm. Cold load is about 0.15 s at 10k, and about 1.2–1.5 s at 100k on this setup.

## Not measured (stated, not hidden)
- Pooled production setup.
- Linux Postgres without Docker Desktop.
- Concurrent recalls.
- Cache invalidation cost on erasure (a design property in D-0024, to be tested at build).
- Recall quality of MiniLM on paraphrases (EXP-0004 dev split, A-0034).
- Memory pressure from caching many scopes per process.
