"""The EXP-0003 runner survives a terminal hang-up under nohup, and still records SIGINT/SIGTERM (and SIGHUP when not
under nohup) as aborts. Regression test for run EXP-0003-20261001T125548Z-8f32eb (aborted by SIGHUP under nohup)."""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _start(tmp_path, under_nohup):
    env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
    cmd = [sys.executable, str(ROOT / "scripts" / "run_exp0003.py"), "run", "--dry-run", "--out", str(tmp_path)]
    log = open(tmp_path / "runner.log", "w")
    return subprocess.Popen((["nohup"] if under_nohup else []) + cmd, cwd=ROOT, env=env, stdout=log,
                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True), tmp_path / "runner.log"


def _progress_lines(log):
    return [line for line in log.read_text().splitlines() if line.startswith('{"progress"')]


def _wait(cond, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def _run_json(tmp_path):
    (d,) = [p for p in tmp_path.iterdir() if p.is_dir() and p.name.startswith("EXP-0003-")]
    return json.loads((d / "run.json").read_text())


def test_under_nohup_a_hangup_is_ignored_and_progress_is_flushed_live(tmp_path):
    proc, log = _start(tmp_path, under_nohup=True)
    try:
        assert _wait(lambda: len(_progress_lines(log)) >= 2), log.read_text()[-2000:]   # flushed while running
        proc.send_signal(signal.SIGHUP)
        seen = len(_progress_lines(log))
        assert _wait(lambda: len(_progress_lines(log)) >= seen + 3), "no progress after SIGHUP"
        assert proc.poll() is None and _run_json(tmp_path)["status"] == "running"
    finally:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=120)
    r = _run_json(tmp_path)
    assert proc.returncode != 0 and (r["status"], r["abort_reason"]) == ("aborted", "signal SIGTERM")


def test_without_nohup_a_hangup_still_aborts_and_is_recorded(tmp_path):
    proc, log = _start(tmp_path, under_nohup=False)
    assert _wait(lambda: len(_progress_lines(log)) >= 1), log.read_text()[-2000:]
    proc.send_signal(signal.SIGHUP)
    proc.wait(timeout=120)
    r = _run_json(tmp_path)
    assert (r["status"], r["abort_reason"]) == ("aborted", "signal SIGHUP")


def test_sigint_aborts_and_is_recorded(tmp_path):
    proc, log = _start(tmp_path, under_nohup=True)
    assert _wait(lambda: len(_progress_lines(log)) >= 1), log.read_text()[-2000:]
    proc.send_signal(signal.SIGINT)
    proc.wait(timeout=120)
    r = _run_json(tmp_path)
    assert (r["status"], r["abort_reason"]) == ("aborted", "signal SIGINT")


def test_progress_lines_appear_one_at_a_time_not_in_buffered_bursts(tmp_path):
    # With block buffering (no flush) the first ~80 lines would land together once 8 KB filled.
    proc, log = _start(tmp_path, under_nohup=True)
    try:
        assert _wait(lambda: len(_progress_lines(log)) >= 1)
        assert len(_progress_lines(log)) < 10
    finally:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=120)


def test_killed_runs_leave_no_database_behind(tmp_path):
    import psycopg
    admin = "host=127.0.0.1 port=54329 user=postgres dbname=postgres password=" + \
        next(line.split(":", 1)[1].strip() for line in (ROOT / "docker-compose.yml").read_text().splitlines()
             if "POSTGRES_PASSWORD:" in line)
    count = lambda: psycopg.connect(admin).execute(  # noqa: E731
        "SELECT count(*) FROM pg_database WHERE datname LIKE 'nacre_exp0003_%'").fetchone()[0]
    before = count()
    for delay in (0.3, 0.8, 1.5):                       # kill at different points during start-up
        proc, _ = _start(tmp_path / str(delay), under_nohup=True) if (tmp_path / str(delay)).mkdir() is None else None
        time.sleep(delay)
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=120)
    assert count() == before
