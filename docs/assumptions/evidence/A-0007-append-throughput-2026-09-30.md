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

## Rescoped target (owner, 2026-09-30) and re-measurement
- **Phase 1 workload:** 1–4 agents plus webhooks per stream, each writing every few seconds.
- **Target:** p99 < 50 ms at ≤ 4 concurrent writers per stream.
- **Stress test:** 16 writers, p99 ceiling 150 ms (regression guard).
- **Setup:** UNPOOLED, as above; the code after the D1 change (stripping outside the lock).

| Mode | Writers | Appends | Appends/s | p50 | p95 | p99 | max | Gapless | Errors |
|---|---|---|---|---|---|---|---|---|---|
| threads | 1 | 400 | 106.0 | 9.6 ms | 11.8 ms | 13.6 ms | 14.4 ms | yes | 0 |
| threads | 2 | 800 | 214.1 | 9.1 ms | 10.9 ms | 12.0 ms | 16.5 ms | yes | 0 |
| threads | 3 | 1,200 | 263.3 | 11.2 ms | 12.5 ms | 14.5 ms | 21.3 ms | yes | 0 |
| threads | **4** | 1,600 | 260.3 | 15.2 ms | 17.0 ms | **18.0 ms** | 27.0 ms | yes | 0 |
| threads | 16 (stress) | 3,200 | 263.1 | 59.5 ms | 64.3 ms | **75.0 ms** | 105.2 ms | yes | 0 |
| processes | 1 | 400 | 100.7 | 9.9 ms | 12.1 ms | 13.7 ms | 78.8 ms* | yes | 0 |
| processes | 2 | 800 | 214.6 | 9.0 ms | 10.1 ms | 12.0 ms | 87.0 ms* | yes | 0 |
| processes | 3 | 1,200 | 259.1 | 11.1 ms | 13.2 ms | 15.0 ms | 100.8 ms* | yes | 0 |
| processes | **4** | 1,600 | 238.7 | 16.3 ms | 18.9 ms | **21.3 ms** | 122.7 ms* | yes | 0 |
| processes | 16 (stress) | 3,200 | 245.1 | 63.9 ms | 72.1 ms | **93.6 ms** | 186.5 ms* | yes | 0 |

\* Process-mode maxima are the first append in each fresh process, which compiles the 229 detection rules
(cold start). They are not steady-state latency, and p99 is unaffected.

**Verdict:**
- ≤ 4 writers: **met** (worst p99 21.3 ms).
- 16-writer stress: **under the 150 ms ceiling** (worst p99 93.6 ms).
- **Still to do:** re-measure once a connection pool is chosen (owner). Machine load average was about 4.5–6 throughout.
