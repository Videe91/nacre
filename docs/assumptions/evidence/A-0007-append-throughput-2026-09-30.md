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

## Re-measurement with the connection pool (D-0006 amendment 1), 2026-09-30
- **Setup: POOLED.** psycopg_pool via `core.db.open_pool`. Threads share one pool sized to the writer count; each
  process owns a pool of one. Every append borrows a connection and opens its own scoped session.
  Latency = borrow → commit.
- A pooled connection never carries scope between principals: tested in
  `tests/scopes/test_open_scoped_session.py::test_a_pooled_connection_never_carries_scope_between_principals`
  (pool size 1, same backend pid across principals, including after an error).

| Mode | Writers | Appends | Appends/s | p50 | p95 | p99 | max | Gapless | Errors |
|---|---|---|---|---|---|---|---|---|---|
| threads | 1 | 400 | 95.0 | 9.8 ms | 14.1 ms | 18.9 ms | 20.8 ms | yes | 0 |
| threads | 2 | 800 | 203.8 | 9.6 ms | 12.1 ms | 17.4 ms | 26.7 ms | yes | 0 |
| threads | **4** | 1,600 | 243.6 | 15.8 ms | 19.4 ms | **24.0 ms** | 34.7 ms | yes | 0 |
| threads | 16 (stress) | 3,200 | 253.3 | 61.8 ms | 71.9 ms | **87.8 ms** | 107.1 ms | yes | 0 |
| processes | 1 | 400 | 86.6 | 10.8 ms | 15.0 ms | 22.0 ms | 109.5 ms* | yes | 0 |
| processes | 2 | 800 | 198.1 | 9.7 ms | 11.6 ms | 14.0 ms | 100.9 ms* | yes | 0 |
| processes | **4** | 1,600 | 248.4 | 15.7 ms | 17.8 ms | **19.0 ms** | 123.2 ms* | yes | 0 |
| processes | 16 (stress) | 3,200 | 235.8 | 67.2 ms | 75.4 ms | **79.4 ms** | 184.1 ms* | yes | 0 |

\* Cold-start maxima, as before (the first append in each fresh process compiles the rules).

**Verdict, pooled (the official gate item 6 result):**
- ≤ 4 writers: p99 **≤ 24.0 ms** (target < 50 ms, **met**).
- 16-writer stress: p99 **≤ 87.8 ms** (ceiling 150 ms, **met**).

The pool adds a few ms against the unpooled runs. Machine load average was about 4.2–5.4.

## Gate run, 2026-09-30 (code at 4934894, with the D-0015 attachment lock), POOLED
- Same script, same pooled setup as above (`core.db.open_pool`). The benchmark appends text events, so the new
  per-attachment lock is not on this path.

| Mode | Writers | Appends | Appends/s | p50 | p95 | p99 | max | Gapless | Errors |
|---|---|---|---|---|---|---|---|---|---|
| threads | **4** | 800 | 244.8 | 16.0 ms | 19.1 ms | **22.7 ms** | 38.9 ms | yes | 0 |
| processes | **4** | 800 | 245.5 | 15.6 ms | 18.2 ms | **19.6 ms** | 95.1 ms* | yes | 0 |
| threads | 16 (stress) | 1,600 | 233.8 | 67.6 ms | 74.9 ms | **93.3 ms** | 114.9 ms | yes | 0 |

\* Cold start, as before.

**Official gate item 6 evidence (owner, 2026-09-30):**
- The pooled measurement from e3206cf above (≤ 4 writers p99 ≤ 24.0 ms; 16-writer stress p99 ≤ 87.8 ms).
- This gate run confirms it (≤ 4 writers p99 ≤ 22.7 ms; stress p99 93.3 ms). Both are pooled. The unpooled tables
  are history only.
- Correction: the gate-run note in CURRENT.md first called these numbers "unpooled", copying a stale test
  docstring. The script has been pooled since e3206cf; the note is corrected.
