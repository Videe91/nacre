"""
EXP-0003 runner (pre-registered in the EXP-0003 file under docs/experiments; Phase 2 gate item 3; recorded replay =
gate item 4). Nacre arm + same-run no-memory control on frozen tasks 014-016, k = 3, gpt-4o-mini-2024-07-18.

    .venv/bin/python scripts/run_exp0003.py run                 # owner, OPENAI_API_KEY exported in this shell
    .venv/bin/python scripts/run_exp0003.py run --dry-run       # plumbing only, no key, no model
    .venv/bin/python scripts/run_exp0003.py run --recorded RUN_DIR   # replay a live run's fixtures (gate item 4)

Rules it enforces: frozen suite verified first (P1); a fresh database per run (k); one scope per family; the key is
read by the OpenAI SDK only and never printed or stored; every call is recorded (D-0021/D-0022) and exported as
fixtures; SIGINT/SIGTERM/SIGHUP mark the run `aborted`; no reruns (a failed bar is a result). Results go to
~/Desktop/nacre-runs/EXP-0003-<utc>-<id>/ (outside the repo).
"""
import argparse
import json
import os
import secrets
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from nacre.core.db import DbRole, connect  # noqa: E402
from nacre.core.model_provider import ModelResponse, Usage  # noqa: E402
from nacre.eval.run_phase2_gate import SAFETY_METRICS, probe_cross_scope, run_family  # noqa: E402
from nacre.eval.verify_frozen_suite import verify_frozen_suite  # noqa: E402
from nacre.keys.local_file_root_key import LocalFileRootKeyProvider  # noqa: E402
from nacre.models.load_model_call_fixtures import export_model_calls, load_model_calls  # noqa: E402
from nacre.models.recorded_provider import RecordedProvider  # noqa: E402
from nacre.models.set_model_policy import set_model_policy  # noqa: E402
from nacre.schema.apply_migrations import apply_migrations  # noqa: E402
from nacre.scopes.bootstrap_org import bootstrap_org  # noqa: E402
from nacre.scopes.open_scoped_session import open_scoped_session  # noqa: E402
from nacre.scopes.register_scope import ScopeKind, register_scope  # noqa: E402
from nacre.scopes.set_access import set_access  # noqa: E402



def _dev_admin_dsn() -> str:
    """The local Docker dev database (D-0006): credentials are read from docker-compose.yml, never written here."""
    compose = (Path(__file__).resolve().parent.parent / "docker-compose.yml").read_text()
    password = next(line.split(":", 1)[1].strip() for line in compose.splitlines() if "POSTGRES_PASSWORD:" in line)
    return make_conninfo(host="127.0.0.1", port=54329, user="postgres", password=password, dbname="postgres")


ADMIN = os.environ.get("NACRE_TEST_DSN") or _dev_admin_dsn()
REGRESSION = ROOT / "tests" / "regression"
MODEL = "gpt-4o-mini-2024-07-18"
SETS, K = (14, 15, 16), 3
BAR = {"pooled_min": 162, "per_set_min": 51, "over_control_min_trials": 90}
EXP0001 = {"B_mnexa": 178, "C_no_memory": 21, "per_set_B": {"014": 59, "015": 59, "016": 60}}


class DryProvider:
    """Plumbing only: proposes nothing and answers transfers with a fixed text (no model, no key)."""
    name, replay = "openai", False

    def complete(self, request, *, timeout_s):
        text = '{"propositions": []}' if request.purpose.startswith("sleep.") else "DRY RUN"
        return ModelResponse(text, "completed", Usage(1, 1, None), None, MODEL, 0)


class NoLiveCalls:
    """Recorded mode: any live call is a hard failure (gate item 4)."""
    name, replay = "openai", False

    def complete(self, request, *, timeout_s):
        raise AssertionError(f"recorded mode attempted a live call ({request.purpose})")


class StreamReplay:
    """Replays the recordings loaded into one family's stream, in recorded order."""
    name, replay = "recorded", True

    def __init__(self, open_session, provider, stream):
        with open_session() as s:
            self._rp = RecordedProvider(s, provider, [stream])

    def complete(self, request, *, timeout_s):
        return self._rp.complete(request, timeout_s=timeout_s)


def _dsn(**kw):
    info = conninfo_to_dict(ADMIN)
    info.update(kw)
    return make_conninfo(**info)


def _git(*a):
    return subprocess.run(["git", "-C", str(ROOT), *a], capture_output=True, text=True).stdout.strip()


def one_run(rep, rdir, record, mode, recorded_dir):
    name = f"nacre_exp0003_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{name}"')
    try:
        db = _dsn(dbname=name)
        with connect(DbRole.MIGRATOR, dsn=db) as conn:
            apply_migrations(conn)
        with psycopg.connect(db, autocommit=True) as c:
            c.execute("ALTER ROLE nacre_app LOGIN PASSWORD 'nacre_exp_only'")
        app = _dsn(dbname=name, user="nacre_app", password="nacre_exp_only")
        provider = LocalFileRootKeyProvider.initialise(Path(tempfile.mkdtemp()) / "rootkeys")
        owner = uuid.uuid4()
        with connect(DbRole.MIGRATOR, dsn=db) as admin:
            org = bootstrap_org(admin, provider, owner_principal_id=owner, idempotency_key=str(uuid.uuid4()))

        @contextmanager
        def open_as(principal):
            with connect(DbRole.APP, dsn=app) as conn, open_scoped_session(conn, principal) as s:
                yield s
        open_session = lambda: open_as(owner)  # noqa: E731
        with open_session() as s:
            set_model_policy(s, provider, org_id=org, allowed=[("openai", MODEL)], idempotency_key=str(uuid.uuid4()))
        live = DryProvider() if mode == "dry" else NoLiveCalls() if mode == "recorded" else None
        if mode == "live":
            from nacre.models.openai_responses_provider import OpenAIResponsesProvider
            live = OpenAIResponsesProvider()
        trials, safety, cost, calls, fallback, streams = [], dict.fromkeys(SAFETY_METRICS, 0), Decimal(0), 0, 0, []
        for n in SETS:
            families = json.loads((REGRESSION / "mnexa" / "tasks" / f"tasks_{n:03d}.json").read_text())["families"]
            for fam in families:
                stream = uuid.uuid4()
                with open_session() as s:
                    register_scope(s, provider, org_id=org, stream_id=stream, kind=ScopeKind.PROJECT, idempotency_key=str(uuid.uuid4()))
                with open_session() as s:
                    set_access(s, provider, org_id=org, principal_id=owner, stream_id=stream, can_read=True, can_append=True,
                               idempotency_key=str(uuid.uuid4()))
                fixture = rdir / "fixtures" / f"rep{rep}" / f"{n:03d}" / f"{fam['id']}.jsonl"
                transfer = live
                if mode == "recorded":
                    with open_session() as s:
                        load_model_calls(s, provider, stream, recorded_dir / "fixtures" / f"rep{rep}" / f"{n:03d}" / f"{fam['id']}.jsonl")
                    transfer = StreamReplay(open_session, provider, stream)
                r = run_family(open_session, provider, live, transfer, stream, fam, n)
                fixture.parent.mkdir(parents=True, exist_ok=True)
                if mode != "recorded":
                    with open_session() as s:
                        export_model_calls(s, provider, stream, fixture)
                trials.append({"rep": rep, "set": f"{n:03d}", "family": fam["id"], "N": r.n_pass, "C": r.c_pass,
                               "N_attempts": r.n_attempts, "C_attempts": r.c_attempts, "memory_items": len(r.memory)})
                for k, v in r.safety.items():
                    safety[k] += v
                cost += r.sleep.cost_usd
                calls += r.sleep.calls_live
                fallback += r.sleep.fallback
                streams.append(stream)
                record["progress"] = f"rep{rep} {n:03d} {fam['id']}"
                print(json.dumps({"progress": record["progress"], "at": datetime.now(UTC).isoformat(timespec="seconds")}),
                      flush=True)
        stranger = uuid.uuid4()
        with open_session() as s:
            set_access(s, provider, org_id=org, principal_id=stranger, stream_id=streams[0], can_read=True, can_append=False,
                       idempotency_key=str(uuid.uuid4()))
        safety["cross_scope_memory"] += probe_cross_scope(open_as, provider, stranger, streams[-1])
        return trials, safety, str(cost), calls, fallback
    finally:
        with psycopg.connect(ADMIN, autocommit=True) as c:
            c.execute(f'DROP DATABASE "{name}" WITH (FORCE)')


def summarize(trials, safety):
    per = {f"{n:03d}": {"N": sum(t["N"] for t in trials if t["set"] == f"{n:03d}"),
                        "C": sum(t["C"] for t in trials if t["set"] == f"{n:03d}"),
                        "trials": sum(1 for t in trials if t["set"] == f"{n:03d}")} for n in SETS}
    N, C = sum(v["N"] for v in per.values()), sum(v["C"] for v in per.values())
    checks = {"pooled": N >= BAR["pooled_min"], "per_set": all(v["N"] >= BAR["per_set_min"] for v in per.values()),
              "over_control": N - C >= BAR["over_control_min_trials"], "safety_zero": all(v == 0 for v in safety.values())}
    return {"N": N, "C": C, "trials": len(trials), "per_set": per, "bar": BAR, "checks": checks, "PASS": all(checks.values()),
            "safety": safety, "EXP-0001_reference_not_a_gate": EXP0001}


def main(argv):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--recorded", type=Path)
    r.add_argument("--out", type=Path, default=Path.home() / "Desktop" / "nacre-runs")
    a = ap.parse_args(argv)
    mode = "dry" if a.dry_run else "recorded" if a.recorded else "live"
    if mode == "live" and not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is not set in this shell (export it yourself; it is never printed).")
    suite = verify_frozen_suite(REGRESSION)
    if not suite.ok:
        raise SystemExit(f"frozen suite does not match its manifests: {suite.problems[:3]}")
    rdir = a.out / f"EXP-0003-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{secrets.token_hex(3)}-{mode.upper()}"
    rdir.mkdir(parents=True)
    print(f"run folder: {rdir}", flush=True)
    record = {"experiment": "EXP-0003", "mode": mode, "model": MODEL, "k": K, "sets": [f"{n:03d}" for n in SETS],
              "nacre_head": _git("rev-parse", "HEAD"), "dirty": bool(_git("status", "--porcelain")),
              "recorded_from": str(a.recorded) if a.recorded else None, "status": "running", "runs": []}
    save = lambda: (rdir / "run.json").write_text(json.dumps(record, indent=1))  # noqa: E731

    def stop(signum, _f):
        record["status"], record["abort_reason"] = "aborted", f"signal {signal.Signals(signum).name}"
        save()
        raise SystemExit(f"aborted by {signal.Signals(signum).name}; recorded in {rdir}/run.json")
    # SIGINT and SIGTERM abort the run (recorded). SIGHUP aborts too, UNLESS it is already ignored at startup (as under
    # nohup): then it stays ignored, so a terminal hang-up cannot kill a detached run (EXP-0003 run 8f32eb, 2026-10-01).
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, stop)
    if signal.getsignal(signal.SIGHUP) is not signal.SIG_IGN:
        signal.signal(signal.SIGHUP, stop)
    save()
    all_trials, all_safety = [], dict.fromkeys(SAFETY_METRICS, 0)
    for rep in range(1, K + 1):
        t0 = time.time()
        trials, safety, cost, calls, fallback = one_run(rep, rdir, record, mode, a.recorded)
        all_trials += trials
        for k, v in safety.items():
            all_safety[k] += v
        record["runs"].append({"rep": rep, "seconds": round(time.time() - t0, 1), "sleep_cost_usd": cost,
                               "sleep_calls_live": calls, "fallback_records": fallback})
        save()
        print(json.dumps(record["runs"][-1]), flush=True)
    (rdir / "trials.json").write_text(json.dumps(all_trials, indent=1))
    summary = summarize(all_trials, all_safety)
    (rdir / "summary.json").write_text(json.dumps(summary, indent=1))
    record["status"] = "complete"
    save()
    print(json.dumps(summary, indent=1), flush=True)
    print(f"run folder: {rdir}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
