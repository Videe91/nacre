# A-0007 measurement: append throughput on one stream (Phase 1 gate item 6), 2026-09-30

- **Connection setup: UNPOOLED.** No psycopg_pool, as the owner requires this be stated. Each writer owns
  one reused connection. Every append opens its own scoped session (resolve access → SET LOCAL → append →
  COMMIT): the per-request production path. Latency = session open → commit.
- **Environment:** docker `postgres:17.11` on the same Mac (arm64, 14 cores), Python 3.14.3. Machine load
  average during the runs was about 5–6, from other work. Script: `scripts/bench_append_throughput.py`.
- **Provisional target (A-0007):** ≥ 100 appends/s per stream and p99 < 50 ms.

| Run | Writers | Appends | Appends/s | p50 | p95 | p99 | max | Gapless | Errors |
|---|---|---|---|---|---|---|---|---|---|
| 1. first code (lock taken before stripping) | 1 thread | 300 | 105.7 | 9.4 ms | 11.5 ms | 13.1 ms | 21.1 ms | yes | 0 |
| 2. first code | 16 threads | 3,200 | 167.8 | 94.1 ms | 102.5 ms | **111.5 ms** | 124.3 ms | yes | 0 |
| 3. stripping + key lookup moved before the lock (D1) | 1 thread | 300 | 107.0 | 9.2 ms | 11.1 ms | 12.9 ms | 17.1 ms | yes | 0 |
| 4. same | 16 threads | 3,200 | 233.9 | 67.0 ms | 78.5 ms | **83.5 ms** | 102.2 ms | yes | 0 |
| 5. same | 16 processes | 3,200 | 238.9 | 66.1 ms | 73.8 ms | **80.9 ms** | 197.9 ms | yes | 0 |

## Reading
- **Throughput target: met** in every run (105–239 appends/s on a single stream).
- **p99 < 50 ms: met with 1 writer (≈ 13 ms); NOT met with 16 concurrent writers on one stream (≈ 80 ms).**
- Processes ≈ threads, so the Python GIL is not the limit. The limit is the per-stream serialisation that
  D-0003 chose (gapless sequence + chain): about 4.2 ms of locked work per append (idempotency check, head
  read, key-use count, encryption, insert with its linkage trigger, and the durable COMMIT, all under the
  lock). 16 queued writers therefore wait about 16 × 4.2 ms.
- Run 1 → run 3 was a D1 change: work that needs no sequence number (secret stripping, subject, key
  resolution) moved before the lock. p99 under 16 writers fell from 111.5 to about 80 ms.
- Getting below 50 ms at 16-way single-stream contention needs one of D-0003's own named remedies
  ("per-stream sub-sequences or batched appends"), which is a new ADR. Owner decision.
