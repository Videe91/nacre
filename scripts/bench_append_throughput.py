"""
A-0007 benchmark (Phase 1 gate item 6): append throughput and latency on ONE stream under concurrency.
Not product code. Needs the Docker test Postgres (docker compose up -d --wait).

    .venv/bin/python scripts/bench_append_throughput.py [writers] [appends_per_writer] [threads|processes]

Connection setup: UNPOOLED (no psycopg_pool, D-0006; the owner requires pooled-vs-unpooled be stated).
Each writer thread owns one connection, reused. Every append opens its own scoped session (resolve access,
SET LOCAL, append, COMMIT), i.e. the per-request production path. Latency = session open → commit.
Prints a JSON result; the caller records it in evidence.
"""
import json
import os
import platform
import statistics
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from nacre.core.db import DbRole, connect  # noqa: E402
from nacre.core.event import ActorKind, EventType, PayloadType, Source  # noqa: E402
from nacre.keys.local_file_root_key import LocalFileRootKeyProvider  # noqa: E402
from nacre.ledger.append_event import AppendRequest, append_event  # noqa: E402
from nacre.schema.apply_migrations import apply_migrations  # noqa: E402
from nacre.scopes.open_scoped_session import open_scoped_session  # noqa: E402

ADMIN = os.environ.get("NACRE_TEST_DSN", "postgresql://postgres:nacre_dev@127.0.0.1:54329/postgres")


def _dsn(**kw):
    info = conninfo_to_dict(ADMIN)
    info.update(kw)
    return make_conninfo(**info)


def _process_worker(app, provider_dir, stream, principal, per_writer, start_evt, out):
    provider = LocalFileRootKeyProvider(Path(provider_dir))
    mine = []
    with connect(DbRole.APP, dsn=app) as conn:
        start_evt.wait()
        for _ in range(per_writer):
            t0 = time.perf_counter()
            with open_scoped_session(conn, principal) as s:
                append_event(s, provider, AppendRequest(
                    stream_id=stream, event_type=EventType.ACTION, payload_type=PayloadType.TEXT,
                    actor_kind=ActorKind.AGENT, actor_id=principal, source=Source.TOOL,
                    idempotency_key=str(uuid.uuid4()), content="ran pytest: 553 passed, 25 deselected"))
            mine.append(time.perf_counter() - t0)
    out.put(mine)


def main(writers=16, per_writer=200, mode="threads"):
    name = f"nacre_bench_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{name}"')
    try:
        db = _dsn(dbname=name)
        with connect(DbRole.MIGRATOR, dsn=db) as conn:
            apply_migrations(conn)
        with psycopg.connect(db, autocommit=True) as c:
            c.execute("ALTER ROLE nacre_app LOGIN PASSWORD 'nacre_bench_only'")
            org, stream, principal = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            for s, kind in ((org, "org"), (stream, "project")):
                c.execute("INSERT INTO scopes.scopes (stream_id, kind, org_id, source_event_id) VALUES (%s,%s,%s,%s)",
                          (s, kind, org, uuid.uuid4()))
            c.execute("""INSERT INTO scopes.scope_grants (principal_id, stream_id, org_id, can_read, can_append,
                         source_event_id, source_seq) VALUES (%s,%s,%s,true,true,%s,1)""", (principal, stream, org, uuid.uuid4()))
        app = _dsn(dbname=name, user="nacre_app", password="nacre_bench_only")
        provider_dir = Path(tempfile.mkdtemp()) / "rootkeys"
        provider = LocalFileRootKeyProvider.initialise(provider_dir)

        def request():
            return AppendRequest(stream_id=stream, event_type=EventType.ACTION, payload_type=PayloadType.TEXT,
                                 actor_kind=ActorKind.AGENT, actor_id=principal, source=Source.TOOL,
                                 idempotency_key=str(uuid.uuid4()), content="ran pytest: 553 passed, 25 deselected")

        with connect(DbRole.APP, dsn=app) as conn, open_scoped_session(conn, principal) as s:
            append_event(s, provider, request())                 # warm-up: creates master and data keys

        latencies, errors, barrier = [], [], threading.Barrier(writers)

        def worker():
            mine = []
            try:
                with connect(DbRole.APP, dsn=app) as conn:
                    barrier.wait()
                    for _ in range(per_writer):
                        t0 = time.perf_counter()
                        with open_scoped_session(conn, principal) as s:
                            append_event(s, provider, request())
                        mine.append(time.perf_counter() - t0)
            except Exception as exc:  # noqa: BLE001 - reported
                errors.append(repr(exc))
            latencies.extend(mine)

        if mode == "processes":
            import multiprocessing as mp
            ctx = mp.get_context("spawn")
            start_evt, out = ctx.Event(), ctx.Queue()
            procs = [ctx.Process(target=_process_worker, args=(app, str(provider_dir), stream, principal, per_writer,
                                                                start_evt, out)) for _ in range(writers)]
            for pr in procs:
                pr.start()
            time.sleep(2.0)                                   # let every process import and connect
            start = time.perf_counter()
            start_evt.set()
            for _ in procs:
                latencies.extend(out.get())
            elapsed = time.perf_counter() - start
            for pr in procs:
                pr.join()
                if pr.exitcode:
                    errors.append(f"process exit {pr.exitcode}")
        else:
            threads = [threading.Thread(target=worker) for _ in range(writers)]
            start = time.perf_counter()
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            elapsed = time.perf_counter() - start
        with psycopg.connect(db) as c:
            seqs = [r[0] for r in c.execute("SELECT commit_seq FROM ledger.events WHERE stream_id = %s ORDER BY 1", (stream,))]
        q = statistics.quantiles(latencies, n=100)
        return {
            "connection_setup": f"UNPOOLED (one reused connection per writer {mode[:-1]}; no psycopg_pool)",
            "mode": mode,
            "writers": writers, "appends": len(latencies), "errors": errors[:5], "error_count": len(errors),
            "elapsed_s": round(elapsed, 3), "appends_per_s": round(len(latencies) / elapsed, 1),
            "latency_ms": {"p50": round(q[49] * 1000, 1), "p95": round(q[94] * 1000, 1), "p99": round(q[98] * 1000, 1),
                           "max": round(max(latencies) * 1000, 1)},
            "gapless": seqs == list(range(1, len(seqs) + 1)), "rows": len(seqs),
            "environment": {"postgres": "docker postgres:17.11 (local)", "python": platform.python_version(),
                            "machine": platform.platform(), "cpu_count": os.cpu_count(),
                            "load_avg_1_5_15": [round(x, 2) for x in os.getloadavg()]},
            "target_A0007_provisional": ">= 100 appends/s per stream, p99 < 50 ms",
        }
    finally:
        with psycopg.connect(ADMIN, autocommit=True) as c:
            c.execute(f'DROP DATABASE "{name}" WITH (FORCE)')


if __name__ == "__main__":
    args = [int(a) for a in sys.argv[1:3]] + sys.argv[3:4]
    print(json.dumps(main(*args), indent=1))
