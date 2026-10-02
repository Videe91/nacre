# D-0025 §9 measurement: recall latency (Phase 3 gate item 7), 2026-10-02

**Status: NOT MEASURED. No gated number exists yet.** The 10k pool could not be built through the real write path
in reasonable time: the projected build time is about 16 hours. A 3k intermediate pool stopped at 2,800 lessons when
another session changed the cluster-wide `nacre_app` password. The machine never got quiet (load ≤ 4) during the
session, so no measurement here counts as evidence. Nothing below is a pass or a fail against §9.

## Setup (as built)
- Script: `scripts/bench_recall_latency.py`. Bench tests: `tests/recall/test_recall_latency.py` (`-m bench`, gated
  at 10k; a missing pool fails the test, it never skips it).
- **Connection setup: POOLED.** Every timed connection comes from `core.db.open_pool` (psycopg_pool). The classes
  below are taken from the objects actually used in the trial run:
  - pool `psycopg_pool.pool.ConnectionPool`;
  - connection `psycopg.Connection`;
  - embedder `nacre.recall.embedder_worker.WorkerEmbedder` (`default_embedder()`, D-0024 amendment 1). Each cold
    process had its own worker pid.
- **Pool:** N grounded beliefs written through `record_decision -> record_outcome -> propose_lesson ->
  promote_if_supported`, the same calls as `tests/recall/recall_kit.py belief()`, so every version has its index
  entry exactly as in production.
  - Lesson texts are deterministic (seed 20261002): 40 components × 40 actions × 25 conditions × 10 reasons.
  - Each nucleus is unique, so each lesson is one object.
  - About 10% of lessons are reviewed by one of 5 people, so the pool spans several contributor-set keys.
  - Lessons are spread evenly over 5 granted streams (agent, user, project, team, org). The request merges all
    five, narrowest first, and the trace goes to the project stream.
  - The merged pool is counted with `merge_scopes` at the snapshot, never assumed. 600 → 600 and 1,000 → 1,000.
- **Queries:** 100 distinct questions (seed 7) built from the same vocabulary.
- **Fixed values:** `tau_strong_q = 6000`, the same value the recall tests use (coverage hardly affects timing);
  `Budget()` default (10 items, 4,000 chars).
- **Timed paths:**
  - e2e = pool borrow → `recall_context` (trace committed) → connection returned;
  - read side = borrow → `open_scoped_session(snapshot=True)` + `freeze_snapshot` + `build_frame`;
  - cold = a fresh process with a new `IndexCache` and a new, unstarted `WorkerEmbedder`, timing its first
    `recall_context`. Imports and pool open (`pool.wait()`) are measured separately, and so is process start → first
    frame.
- **Environment:**
  - Apple M3 Max, 14 cores, 36 GB, macOS 26.6.2;
  - docker `postgres:17.11`, 14 vCPU in the VM;
  - Python 3.14.3;
  - code at 09f3f56, plus the new script.

## Finding 1: the real write path makes the pool O(N²) to build (blocker for 10k and 100k)
Each belief decrypts the whole stream 3 times:
- `propose_lesson` calls `read_stream` once;
- `promote_if_supported` calls `read_stream` and then `read_version_events`, which is another `read_stream`.

Each event decrypt also calls `load_key`, which reads the root key file and unwraps the master key with no cache.
A profile at about 120 beliefs per stream showed this:
- 2.28 s per belief, with 5 beliefs causing 5,848 decrypts;
- about 55% of the time in `load_key`/`_master_key`.

A transaction holds each stream's append lock until COMMIT, so writes to one stream serialise. Build parallelism is
therefore at most one writer per stream: 5 here.

Build timings (5 workers, one per stream; machine load 5–13 throughout from this build and unrelated processes):

| Pool reached | Round of 100 lessons |
|---|---|
| 700 | 84 s |
| 1,000 | 237 s (load 13) |
| 1,500 | 147 s |
| 2,000 | 191 s |
| 2,500 | 241 s |
| 2,800 | 295 s |

So each round of 100 costs about 0.11–0.12 s × the current pool size. Totals:
- 600: 223 s;
- 600 → 1,000: 484 s;
- 1,000 → 2,800: 68.5 min.

**Projection (same machine and load, 5 scopes):**
- 10k: about 16 h from empty, or about 14 h from the 2,800 pool;
- 100k: about 67 days.

With 2 or 3 scopes instead of 5, the build is slower (cost scales with N²/streams).

## Finding 2: two write-path behaviours seen while building
1. **Deadlock.** A transaction that appends to more than one stream deadlocks against another such transaction.
   - Cause: each holds its stream advisory lock (`ledger.stream_lock_key`) until COMMIT and then waits on the
     other's lock or `keys.data_keys` insert.
   - Postgres log, 2026-10-02 12:48 UTC: "waits for ExclusiveLock on advisory lock ... blocked by process ...
     INSERT INTO keys.data_keys".
   - The bench avoids it with one stream per transaction. Production sessions that write two streams in one
     transaction would hit it.
2. **Shared login.** `ALTER ROLE nacre_app ... PASSWORD` is cluster-wide. The test fixtures and this bench each set
   their own password, so running them at the same time breaks the other's new connections. That is how the 3k
   build died at 2,800, at 19:4x local time.

## Diagnostic trial only (NOT evidence: pool 600, load 7.2–7.8, 30 warm samples, 3 cold runs)

| Path | p50 | p95 | max |
|---|---|---|---|
| warm e2e | 127.5 ms | 136.2 ms | 139.6 ms |
| warm read side | 95.1 ms | 103.4 ms | 104.5 ms |
| cold first recall | 537.0 ms | 554.8 ms | 556.8 ms |
| cold second recall | 132.0 ms | 136.6 ms | 137.2 ms |
| process start → first frame | 729.9 ms | 759.1 ms | 762.3 ms |

Other cold-process figures: imports about 180 ms; pool open about 14 ms.

These are under load at 1/17 of the gated pool and prove nothing either way. But the warm read side, at about
95–103 ms with only 600 entries, is already about 2× the 50 ms target. A profile of 20 warm read-side calls at 600
showed where the time goes:
- about 110 SQL round trips per recall;
- `rank_candidates` re-tokenises every candidate text in Python on every request (601 `tokens()` calls per recall);
- `assemble_frame` calls `read_stream` once per frame item (10 per recall).

If a quiet 10k run confirms the miss, D-0025 §9's remedy order applies:
1. an in-memory inverted index for lexical;
2. group commit for trace appends (A-0019);
3. Rust (SPEC).

It is never a plaintext index. No remedy was implemented.

## Commands
```
.venv/bin/python -B scripts/bench_recall_latency.py build 600 5 15
.venv/bin/python -B scripts/bench_recall_latency.py measure 600 30 3           # trial above (under load)
.venv/bin/python -B scripts/bench_recall_latency.py build 1000 5 30            # cloned from 600
.venv/bin/python -B scripts/bench_recall_latency.py build 3000 5 100           # cloned from 1000; died at 2,800
# to finish (resumable; each N is cloned from the largest smaller cached pool):
.venv/bin/python -B scripts/bench_recall_latency.py build 10000 5 <minutes>
.venv/bin/python -B scripts/bench_recall_latency.py measure 10000 300 20       # quiet machine, load <= 4
.venv/bin/python -m pytest -q -rf -p no:cacheprovider -m bench tests/recall/test_recall_latency.py
```
Cached pools: `nacre_bench_recall_{600,1000,3000}` and `~/.cache/nacre/bench_recall/`. The 3000 pool is at 2,800,
and a re-run may repeat up to one round of 100.
