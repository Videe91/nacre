"""A-0007 benchmark tests (owner-rescoped target, 2026-09-30). Timing-sensitive: `pytest -m bench`, quiet machine.
POOLED setup (core.db.open_pool, the production setup), as scripts/bench_append_throughput.py documents. Runs the script as a subprocess, so process mode
(spawn) imports it normally."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/bench_append_throughput.py"


def _bench(writers, per_writer, mode):
    out = subprocess.run([sys.executable, str(SCRIPT), str(writers), str(per_writer), mode],
                         capture_output=True, text=True, check=True, timeout=600).stdout
    return json.loads(out)


@pytest.mark.bench
@pytest.mark.parametrize("mode", ["threads", "processes"])
def test_p99_under_50ms_at_4_writers_per_stream(pg_dsn, mode):
    r = _bench(4, 200, mode)
    assert r["error_count"] == 0 and r["gapless"]
    assert r["latency_ms"]["p99"] < 50, r


@pytest.mark.bench
def test_16_writer_stress_p99_under_150ms(pg_dsn):
    r = _bench(16, 100, "threads")
    assert r["error_count"] == 0 and r["gapless"]
    assert r["latency_ms"]["p99"] < 150, r
