"""
D-0025 §9 benchmark (Phase 3 gate item 7): recall latency at a merged pool of N entries.
Not product code. Needs the Docker test Postgres (docker compose up -d --wait).

    .venv/bin/python scripts/bench_recall_latency.py build   N [workers] [max_minutes]   # grow or resume the pool for N
    .venv/bin/python scripts/bench_recall_latency.py measure N [warm_samples] [cold_runs]
    .venv/bin/python scripts/bench_recall_latency.py drop    N             # drop the cached pool database
    (internal) ... cold N                                                    # one cold sample, in a fresh process

Pool: N grounded beliefs written through the REAL write path (record_decision -> record_outcome -> propose_lesson ->
promote_if_supported, the same calls as tests/recall/recall_kit.py belief()), so every version gets its encrypted
index entry exactly as in production (index_version with default_embedder()). Lesson texts are generated
deterministically (seed POOL_SEED) from a mix of components, actions and conditions, so ranking does real work.
Lessons are spread evenly over 5 granted streams (agent, user, project, team, org); the request merges all five
(narrowest first) and its trace goes to the project stream. ~10% are
reviewed by one of 5 people, so the pool spans several contributor-set keys (D-0023).
The pool is cached per N in database nacre_bench_recall_<N>; its root keys and build progress live in
~/.cache/nacre/bench_recall/<db>/. The build is resumable and time-limited (build N [workers] [max_minutes]); a new N
is cloned from the largest smaller cached pool (the lessons are a fixed prefix sequence).

Connection setup: POOLED (psycopg_pool via core.db.open_pool, D-0006 amendment 1), the production setup, for every
connection the timed code uses. Embedder: default_embedder() (the isolated WorkerEmbedder, D-0024 amendment 1).
The JSON reports the classes of the objects actually used.
Timed paths (each timed from before the pool borrow to after the connection is returned):
  - e2e:  recall_context() (snapshot session + build_frame, then the trace commit): request in, trace committed, frame out;
  - read: open_scoped_session(snapshot=True) + freeze_snapshot + build_frame (no trace commit);
  - cold: a FRESH process (new IndexCache, new WorkerEmbedder not yet started), its first recall_context(); imports
    and pool open (pool.wait()) happen before the timer and are reported separately, as is process start -> frame.
Prints a JSON result; the caller records it in evidence.
"""
import json
import os
import platform
import random
import secrets
import shutil
import statistics
import subprocess
import sys
import time
import uuid
from pathlib import Path

T_PROCESS_START = time.perf_counter()

import psycopg  # noqa: E402
from psycopg import sql  # noqa: E402
from psycopg.conninfo import conninfo_to_dict, make_conninfo  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from nacre.capture.record_decision import record_decision  # noqa: E402
from nacre.capture.record_outcome import Section, record_outcome  # noqa: E402
from nacre.core.db import DbRole, connect, open_pool  # noqa: E402
from nacre.core.event import ActorKind, Source  # noqa: E402
from nacre.keys.local_file_root_key import LocalFileRootKeyProvider  # noqa: E402
from nacre.ledger.append_event import Authorship  # noqa: E402
from nacre.recall.embed_local import DIM  # noqa: E402
from nacre.recall.freeze_snapshot import freeze_snapshot  # noqa: E402
from nacre.recall.index_version import default_embedder  # noqa: E402
from nacre.recall.load_index_cache import IndexCache  # noqa: E402
from nacre.recall.merge_scopes import merge_scopes  # noqa: E402
from nacre.recall.recall_context import RecallRequest, build_frame, recall_context  # noqa: E402
from nacre.recall.assemble_frame import Budget  # noqa: E402
from nacre.scopes.open_scoped_session import open_scoped_session  # noqa: E402
from nacre.stores.promote_if_supported import promote_if_supported  # noqa: E402
from nacre.stores.propose_lesson import propose_lesson  # noqa: E402

T_IMPORTED = time.perf_counter()

ADMIN = os.environ.get("NACRE_TEST_DSN", "")    # the test Postgres superuser DSN (tests/conftest.py pg_dsn); required
CACHE_DIR = Path(os.environ.get("NACRE_BENCH_CACHE", Path.home() / ".cache/nacre/bench_recall"))
# A fresh random password per invocation, handed to child processes through the environment, so no credential is
# written in this file (pre-commit secret hook, 2026-10-02). nacre_app's password is cluster-wide (CURRENT F3).
APP_PASSWORD = os.environ.setdefault("NACRE_BENCH_APP_PASSWORD", secrets.token_hex(16))
POOL_SEED, QUERY_SEED, TAU_STRONG_Q, CONFIG_VERSION = 20261002, 7, 6000, "bench-recall-1"
ROUND = 100
MAX_POOL = 100_000
LEVELS = ("agent", "user", "project", "team", "org")          # narrowest first; one stream each
AGENT, REVIEWER = uuid.UUID(int=21), uuid.UUID(int=22)
PEOPLE = [uuid.UUID(int=900 + i) for i in range(5)]

COMPONENTS = ["payments service", "auth gateway", "billing API", "search indexer", "checkout frontend", "mobile app",
              "CI pipeline", "data warehouse", "notification worker", "user profile service", "inventory service",
              "shipping estimator", "fraud scorer", "admin console", "public REST API", "GraphQL layer",
              "feature flag service", "image resizer", "email sender", "webhook dispatcher", "rate limiter",
              "session store", "audit log", "metrics exporter", "log shipper", "cron scheduler", "export job",
              "import pipeline", "recommendation model", "ledger reconciler", "tax calculator", "invoice generator",
              "SSO bridge", "file upload service", "video transcoder", "chat backend", "analytics dashboard",
              "partner integration", "terraform stack", "kubernetes cluster"]
ACTIONS = [("Pin", "base image by digest"), ("Rotate", "signing certificate"), ("Run", "schema migration check"),
           ("Bump", "connection pool size"), ("Disable", "verbose request logging"), ("Enable", "retry with backoff"),
           ("Add", "idempotency key to writes"), ("Cap", "batch size at 500 rows"), ("Vacuum", "events table"),
           ("Rebuild", "search index"), ("Invalidate", "CDN cache"), ("Freeze", "dependency lockfile"),
           ("Raise", "request timeout to 30 seconds"), ("Lower", "worker concurrency"), ("Drain", "job queue"),
           ("Snapshot", "database volume"), ("Re-run", "flaky integration tests in isolation"),
           ("Use", "read replica for reports"), ("Avoid", "N+1 queries in the list endpoint"),
           ("Shard", "write path by tenant"), ("Encrypt", "backups at rest"), ("Revoke", "stale API tokens"),
           ("Update", "OpenAPI schema"), ("Regenerate", "client SDKs"), ("Warm", "embedding cache"),
           ("Check", "clock skew on the hosts"), ("Validate", "webhook signatures"), ("Debounce", "search input"),
           ("Paginate", "export endpoint"), ("Compress", "large JSON responses"), ("Split", "monolithic migration"),
           ("Gate", "release behind a feature flag"), ("Profile", "memory of the worker"),
           ("Lock", "row before updating the balance"), ("Retry", "failed deliveries hourly"),
           ("Alert", "on queue depth above 1000"), ("Document", "on-call runbook"), ("Restart", "stuck consumers"),
           ("Trim", "old partitions monthly"), ("Mock", "third-party clock in tests")]
CONDITIONS = ["before every release", "after a dependency bump", "when the canary error rate rises",
              "before merging to main", "during the Friday freeze", "after a failed deploy", "on every schema change",
              "when traffic doubles", "before the quarterly audit", "after rotating credentials",
              "whenever the build cache is cold", "before running load tests", "when p99 latency exceeds 300 ms",
              "after a region failover", "before onboarding a new tenant", "when disk usage passes 80 percent",
              "after the nightly backup", "before enabling a new region", "during incident response",
              "when the upstream vendor changes its API", "on the first deploy of the month",
              "after a security advisory", "before cutting a release branch", "when tests flake twice in a row",
              "after upgrading Postgres"]
REASONS = ["", " because the last outage started there", " since reviewers keep flagging it",
           " or the rollback fails", " to keep the SLO", " because the auditors asked", " to stop silent data loss",
           " since it broke staging twice", " or customers see stale data", " to avoid a 2 am page"]
QUESTIONS = ["How should we handle the {o} for the {c}?", "What do we know about the {c} {o}?",
             "Should the {c} {v} its {o} {k}?", "Anything to remember {k} for the {c}?",
             "What went wrong last time with the {o}?", "Is there a rule about the {c} {k}?"]


def _dsn(**kw):
    info = conninfo_to_dict(ADMIN)
    info.update(kw)
    return make_conninfo(**info)


def lessons(n: int) -> list[tuple[str, str, int, uuid.UUID | None]]:
    """The first N of MAX_POOL distinct (correction, nucleus, index into LEVELS, person); fixed by POOL_SEED."""
    rng = random.Random(POOL_SEED)
    space = len(COMPONENTS) * len(ACTIONS) * len(CONDITIONS) * len(REASONS)
    out = []
    for code in rng.sample(range(space), MAX_POOL)[:n]:
        code, r = divmod(code, len(REASONS))
        code, k = divmod(code, len(CONDITIONS))
        c, a = divmod(code, len(ACTIONS))
        verb, obj = ACTIONS[a]
        nucleus = f"{verb} the {COMPONENTS[c]} {obj} {CONDITIONS[k]}{REASONS[r]}"   # unique: one object per lesson
        person = rng.choice(PEOPLE) if rng.random() < 0.10 else None                 # (identity = normalised nucleus)
        out.append((f"{nucleus}.", nucleus, rng.randrange(len(LEVELS)), person))
    return out


def queries(n: int = 100) -> list[str]:
    rng = random.Random(QUERY_SEED)
    out = []
    for _ in range(n):
        verb, obj = rng.choice(ACTIONS)
        out.append(rng.choice(QUESTIONS).format(c=rng.choice(COMPONENTS), o=obj, v=verb.lower(),
                                                k=rng.choice(CONDITIONS)))
    return out


def _belief(s, kp, stream, correction, nucleus, person):
    """Same calls as tests/recall/recall_kit.py belief()."""
    who = (dict(actor_kind=ActorKind.PERSON, actor_id=person, authorship=Authorship.SCOPE_PRINCIPAL) if person
           else dict(actor_kind=ActorKind.SYSTEM, actor_id=REVIEWER, authorship=Authorship.INTEGRATION_RESULT))
    d = record_decision(s, kp, stream_id=stream, actor_kind=ActorKind.AGENT, actor_id=AGENT, source=Source.CHAT,
                        authorship=Authorship.SCOPE_PRINCIPAL, idempotency_key=str(uuid.uuid4()),
                        decision_text="first attempt").envelope
    o = record_outcome(s, kp, stream_id=stream, idempotency_key=str(uuid.uuid4()), outcome_for=d.event_id,
                       success=False, source=Source.REVIEW, **who,
                       sections=(Section("status", "FAIL"), Section("correction", correction))).envelope
    p = propose_lesson(s, kp, stream_id=stream, decision_id=d.event_id, outcome_id=o.event_id, section_index=1,
                       span=(0, len(correction)), nucleus=nucleus).event_id
    return promote_if_supported(s, kp, stream, p)


# ---- pool database (cached per N) ----
def _dbname(n):
    return f"nacre_bench_recall_{n}"


def _dir(n):
    return CACHE_DIR / _dbname(n)


def _progress(n) -> int:
    p = _dir(n) / "progress.txt"
    return int(p.read_text()) if p.exists() else 0


def _ensure_db(n) -> dict:
    """Create (or reuse) the pool database for N, its root keys and its scopes; returns the meta dict. A new pool is
    cloned (CREATE DATABASE ... TEMPLATE, plus its root keys) from the largest smaller pool, since lessons are a
    fixed prefix sequence."""
    name, d = _dbname(n), _dir(n)
    with psycopg.connect(ADMIN, autocommit=True) as c:
        exists = c.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone()
        if exists and (d / "meta.json").exists():
            enable_role_logins(ADMIN, ["nacre_app"], APP_PASSWORD)
            return json.loads((d / "meta.json").read_text())
        if exists:
            c.execute(f'DROP DATABASE "{name}" WITH (FORCE)')      # orphan: its root keys are gone
        smaller = sorted(m for m in (int(x.name.rsplit("_", 1)[1]) for x in CACHE_DIR.glob("nacre_bench_recall_*")
                                     if (x / "meta.json").exists()) if m < n)
        if smaller:
            src = smaller[-1]
            c.execute(f'CREATE DATABASE "{name}" TEMPLATE "{_dbname(src)}"')
            shutil.copytree(_dir(src) / "rootkeys", d / "rootkeys")
            meta = {**json.loads((_dir(src) / "meta.json").read_text()), "n": n, "db": name, "cloned_from": src}
            (d / "progress.txt").write_text(str(_progress(src)))
            (d / "meta.json").write_text(json.dumps(meta))
            enable_role_logins(ADMIN, ["nacre_app"], APP_PASSWORD)
            return meta
        c.execute(f'CREATE DATABASE "{name}"')
    db = _dsn(dbname=name)
    with connect(DbRole.MIGRATOR, dsn=db) as conn:
        from nacre.schema.apply_migrations import apply_migrations
    from nacre.schema.enable_role_logins import enable_role_logins
        apply_migrations(conn)
    LocalFileRootKeyProvider.initialise(d / "rootkeys")
    ids, principal = {k: uuid.uuid4() for k in LEVELS}, uuid.uuid4()
    org = ids["org"]
    with psycopg.connect(db, autocommit=True) as c:
        enable_role_logins(ADMIN, ["nacre_app"], APP_PASSWORD)
        for kind in reversed(LEVELS):
            c.execute("INSERT INTO scopes.scopes (stream_id, kind, org_id, source_event_id) VALUES (%s,%s,%s,%s)",
                      (ids[kind], kind, org, uuid.uuid4()))
        for i, s in enumerate(ids.values()):
            c.execute("""INSERT INTO scopes.scope_grants (principal_id, stream_id, org_id, can_read, can_append,
                         source_event_id, source_seq) VALUES (%s,%s,%s,true,true,%s,%s)""",
                      (principal, s, org, uuid.uuid4(), i + 1))
    meta = {"n": n, "db": name, **{k: str(v) for k, v in ids.items()}, "principal": str(principal),
            "pool_seed": POOL_SEED}
    (d / "meta.json").write_text(json.dumps(meta))
    return meta


def _ctx(meta):
    app = _dsn(dbname=meta["db"], user="nacre_app", password=APP_PASSWORD)
    kp = LocalFileRootKeyProvider(_dir(meta["n"]) / "rootkeys")
    return app, kp, uuid.UUID(meta["principal"]), tuple(uuid.UUID(meta[k]) for k in LEVELS)


_W = {}


def _worker_init(n):
    meta = json.loads((_dir(n) / "meta.json").read_text())
    app, kp, principal, streams = _ctx(meta)
    _W.update(kp=kp, principal=principal, streams=streams, pool=open_pool(DbRole.APP, dsn=app, min_size=1, max_size=1))


def _write_chunk(chunk):
    """One transaction: a chunk of lessons, retried whole on a deadlock (two first-use key creations in opposite
    order, seen 2026-10-02; the transaction rolls back, so a retry writes nothing twice). Returns (retries, s)."""
    t0 = time.perf_counter()
    for attempt in range(6):
        try:
            with _W["pool"].connection() as conn, open_scoped_session(conn, _W["principal"]) as s:
                for correction, nucleus, si, person in chunk:
                    _belief(s, _W["kp"], _W["streams"][si], correction, nucleus, person)
            return attempt, time.perf_counter() - t0
        except psycopg.errors.DeadlockDetected:
            if attempt == 5:
                raise


def build(n, workers=len(LEVELS), max_minutes=90):
    """Grow the pool for N in rounds of ROUND lessons; stop after the round that passes the deadline (resumable). In a
    round each stream's lessons are one transaction in one worker: a transaction holds each stream's append lock
    until COMMIT, so a transaction spanning streams deadlocks against another (seen 2026-10-02), and two on one
    stream serialise anyway. Progress is recorded after a whole round commits; a crash repeats at most one round
    (extra beliefs; the merged pool is counted at measure time, never assumed)."""
    meta = _ensure_db(n)
    all_lessons, done = lessons(n), _progress(n)
    load0, t0, rounds, retries = [round(x, 2) for x in os.getloadavg()], time.perf_counter(), [], 0
    import multiprocessing as mp
    with mp.get_context("spawn").Pool(workers, initializer=_worker_init, initargs=(n,)) as pool:
        while done < n and time.perf_counter() - t0 < max_minutes * 60:
            r0 = time.perf_counter()
            batch = all_lessons[done:done + ROUND]
            chunks = [[x for x in batch if x[2] == si] for si in range(len(LEVELS))]
            retries += sum(r for r, _ in pool.map(_write_chunk, [c for c in chunks if c], chunksize=1))
            done += len(batch)
            (_dir(n) / "progress.txt").write_text(str(done))
            rounds.append((done, round(time.perf_counter() - r0, 2)))
            print(f"built {done}/{n} round {rounds[-1][1]} s load {os.getloadavg()[0]:.2f}", file=sys.stderr, flush=True)
    return {"n_target": n, "lessons_written": done, "complete": done >= n, "workers": workers,
            "deadlock_retries": retries, "build_s_this_run": round(time.perf_counter() - t0, 1), "rounds_done_s": rounds[::max(1, len(rounds) // 20)],
            "load_avg_before": load0, "load_avg_after": [round(x, 2) for x in os.getloadavg()],
            "merged_pool": _pool_size(meta)}


def _request(streams, query):
    return RecallRequest(streams[LEVELS.index("project")], tuple(zip(LEVELS, streams)), query)


def _pool_size(meta):
    app, _, principal, streams = _ctx(meta)
    with connect(DbRole.APP, dsn=app) as conn, open_scoped_session(conn, principal, snapshot=True) as s:
        per = {}
        for c in merge_scopes(s, freeze_snapshot(s, streams), _request(streams, "").scopes):
            per[str(c.stream_id)] = per.get(str(c.stream_id), 0) + 1
    return {"total": sum(per.values()), **{k: per.get(meta[k], 0) for k in LEVELS}}


# ---- measurement ----
def _stats(xs):
    q = statistics.quantiles(xs, n=100, method="inclusive")
    return {"samples": len(xs), "p50": round(q[49] * 1000, 1), "p95": round(q[94] * 1000, 1),
            "p99": round(q[98] * 1000, 1), "max": round(max(xs) * 1000, 1)}


def _recall(pool, kp, principal, streams, query, cache, embedder):
    t0 = time.perf_counter()
    with pool.connection() as conn:
        r = recall_context(conn, kp, principal, _request(streams, query), cache=cache, embedder=embedder,
                           tau_strong_q=TAU_STRONG_Q, config_version=CONFIG_VERSION)
    return time.perf_counter() - t0, r


def _read_side(pool, kp, principal, streams, query, cache, embedder):
    req = _request(streams, query)
    t0 = time.perf_counter()
    with pool.connection() as conn, open_scoped_session(conn, principal, snapshot=True) as s:
        frame = build_frame(s, kp, freeze_snapshot(s, streams), req, principal_id=principal, cache=cache,
                            embedder=embedder, tau_strong_q=TAU_STRONG_Q, budget=Budget(), config_version=CONFIG_VERSION)
    return time.perf_counter() - t0, frame


def cold(n):
    """One cold sample: this process is fresh; imports are done; the timer starts at the first request."""
    meta = json.loads((_dir(n) / "meta.json").read_text())
    app, kp, principal, streams = _ctx(meta)
    q = queries()[int(os.environ.get("NACRE_BENCH_COLD_QUERY", "0")) % 100]
    pool = open_pool(DbRole.APP, dsn=app, min_size=1, max_size=2)
    pool.wait()
    t_pool = time.perf_counter()
    cache, embedder = IndexCache(kp, dim=DIM), default_embedder()
    first, r = _recall(pool, kp, principal, streams, q, cache, embedder)
    t_frame = time.perf_counter()
    second, _ = _recall(pool, kp, principal, streams, q, cache, embedder)
    pool.close()
    return {"first_recall_s": first, "second_recall_s": second, "import_s": T_IMPORTED - T_PROCESS_START,
            "pool_open_s": t_pool - T_IMPORTED, "process_start_to_first_frame_s": t_frame - T_PROCESS_START,
            "items": len(r.frame.body["items"]), "embedder_class": f"{type(embedder).__module__}.{type(embedder).__name__}",
            "embedder_pid": getattr(embedder, "pid", None)}


def measure(n, warm_samples=300, cold_runs=20):
    if not (_dir(n) / "meta.json").exists():
        raise SystemExit(f"no pool {n}: build it first")
    meta = _ensure_db(n)                     # existing pool only: also resets the bench login (tests change it)
    if _progress(n) < n:
        raise SystemExit(f"pool {n} is incomplete ({_progress(n)} lessons written): build it first")
    app, kp, principal, streams = _ctx(meta)
    pool_info = _pool_size(meta)
    qs = queries()
    load_before = [round(x, 2) for x in os.getloadavg()]
    pool = open_pool(DbRole.APP, dsn=app, min_size=1, max_size=4)
    pool.wait()
    cache, embedder = IndexCache(kp, dim=DIM), default_embedder()
    for i in range(30):                                            # warm-up: worker started, cache loaded
        _recall(pool, kp, principal, streams, qs[i % len(qs)], cache, embedder)
    e2e, read, items, coverage = [], [], [], {}
    for i in range(warm_samples):
        dt, r = _recall(pool, kp, principal, streams, qs[i % len(qs)], cache, embedder)
        e2e.append(dt)
        items.append(len(r.frame.body["items"]))
        coverage[r.coverage] = coverage.get(r.coverage, 0) + 1
    for i in range(warm_samples):
        dt, _ = _read_side(pool, kp, principal, streams, qs[i % len(qs)], cache, embedder)
        read.append(dt)
    load_after_warm = [round(x, 2) for x in os.getloadavg()]
    code = {"embedder_class": f"{type(embedder).__module__}.{type(embedder).__name__}",
            "embedder_id": embedder.embedder_id, "embedder_worker_pid": getattr(embedder, "pid", None),
            "pool_class": f"{type(pool).__module__}.{type(pool).__name__}",
            "pool_connection_class": _conn_class(pool)}
    pool.close()
    colds = []
    for i in range(cold_runs):
        out = subprocess.run([sys.executable, "-B", __file__, "cold", str(n)], capture_output=True, text=True,
                             timeout=600, env={**os.environ, "NACRE_BENCH_COLD_QUERY": str(i)})
        if out.returncode:
            raise SystemExit(f"cold run failed: {out.stderr[-2000:]}")
        colds.append(json.loads(out.stdout))
    load_after = [round(x, 2) for x in os.getloadavg()]
    return {
        "connection_setup": "POOLED (psycopg_pool via core.db.open_pool; one client, sequential requests)",
        "code_ran": {**code, "cold_embedder_classes": sorted({c["embedder_class"] for c in colds}),
                     "cold_embedder_worker_pids_distinct": len({c["embedder_pid"] for c in colds})},
        "pool": {"n_requested": n, "merged": pool_info, "scopes_narrowest_first": list(LEVELS), "issuing_stream": "project",
                 "pool_seed": POOL_SEED},
        "queries": {"distinct": len(qs), "seed": QUERY_SEED, "warm_up_recalls": 30},
        "tau_strong_q": TAU_STRONG_Q, "budget": {"items": Budget().items, "chars": Budget().chars},
        "warm_e2e_ms": _stats(e2e), "warm_read_side_ms": _stats(read),
        "cold_first_recall_ms": _stats([c["first_recall_s"] for c in colds]),
        "cold_second_recall_ms": _stats([c["second_recall_s"] for c in colds]),
        "cold_process_start_to_first_frame_ms": _stats([c["process_start_to_first_frame_s"] for c in colds]),
        "cold_import_ms": _stats([c["import_s"] for c in colds]),
        "cold_pool_open_ms": _stats([c["pool_open_s"] for c in colds]),
        "frame_items": {"min": min(items), "max": max(items), "mean": round(statistics.mean(items), 2)},
        "coverage_counts": coverage,
        "targets_D0025_s9_p95": {"warm_e2e_ms": 150, "warm_read_side_ms": 50, "cold_first_recall_ms": 1000,
                                 "gated_at": 10000},
        "environment": {"postgres": "docker postgres:17.11 (local)", "python": platform.python_version(),
                        "machine": platform.platform(), "cpu_count": os.cpu_count(),
                        "load_avg_before": load_before, "load_avg_after_warm": load_after_warm,
                        "load_avg_after_cold": load_after},
    }


def _conn_class(pool):
    with pool.connection() as conn:
        return f"{type(conn).__module__}.{type(conn).__name__}"


def drop(n):
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{_dbname(n)}" WITH (FORCE)')
    shutil.rmtree(_dir(n), ignore_errors=True)
    return {"dropped": _dbname(n)}


if __name__ == "__main__":
    cmd, n, rest = sys.argv[1], int(sys.argv[2]), [int(a) for a in sys.argv[3:]]
    if not ADMIN:
        sys.exit("set NACRE_TEST_DSN to the test Postgres superuser DSN (as tests/conftest.py's pg_dsn uses)")
    with psycopg.connect(ADMIN, autocommit=True) as c:             # every invocation: this run's app password
        enable_role_logins(ADMIN, ["nacre_app"], APP_PASSWORD)
    fn = {"build": build, "measure": measure, "cold": cold, "drop": drop}[cmd]
    print(json.dumps(fn(n, *rest), indent=1))
