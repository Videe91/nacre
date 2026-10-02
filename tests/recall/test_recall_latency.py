"""D-0025 §9 latency tests (Phase 3 gate item 7). Timing-sensitive: `pytest -m bench`, quiet machine (load < 4).
POOLED setup (core.db.open_pool, the production setup) and the isolated WorkerEmbedder, as
scripts/bench_recall_latency.py documents; the script runs as a subprocess and its JSON names the classes it used.
Gated at a merged pool of 10k (100k is reported, not gated). The pool must be built first (hours, resumable):
    .venv/bin/python scripts/bench_recall_latency.py build 10000 5 <max_minutes>
A missing or incomplete pool FAILS these tests; it never skips them."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/bench_recall_latency.py"
CACHE = Path(os.environ.get("NACRE_BENCH_CACHE", Path.home() / ".cache/nacre/bench_recall"))
GATED_POOL = 10_000


@pytest.fixture(scope="module")
def measured(pg_dsn):
    if not (CACHE / f"nacre_bench_recall_{GATED_POOL}" / "meta.json").exists():
        pytest.fail(f"no {GATED_POOL} pool: run `{SCRIPT.name} build {GATED_POOL}` first")
    out = subprocess.run([sys.executable, "-B", str(SCRIPT), "measure", str(GATED_POOL), "300", "20"],
                         capture_output=True, text=True, check=True, timeout=3600,
                         env={**os.environ, "NACRE_TEST_DSN": pg_dsn}).stdout
    r = json.loads(out)
    assert r["pool"]["merged"]["total"] >= GATED_POOL, r["pool"]
    assert r["code_ran"]["embedder_class"] == "nacre.recall.embedder_worker.WorkerEmbedder", r["code_ran"]
    assert r["code_ran"]["pool_class"] == "psycopg_pool.pool.ConnectionPool", r["code_ran"]
    return r


@pytest.mark.bench
def test_warm_recall_end_to_end_p95_at_most_150ms_at_10k(measured):
    assert measured["warm_e2e_ms"]["p95"] <= 150, measured["warm_e2e_ms"]


@pytest.mark.bench
def test_warm_read_side_p95_at_most_50ms_at_10k(measured):
    assert measured["warm_read_side_ms"]["p95"] <= 50, measured["warm_read_side_ms"]


@pytest.mark.bench
def test_cold_first_recall_p95_at_most_1s_at_10k(measured):
    assert measured["cold_first_recall_ms"]["p95"] <= 1000, measured["cold_first_recall_ms"]
