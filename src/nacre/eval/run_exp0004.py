"""
Functionality: Run EXP-0004 end to end (owner-run): verify the frozen split, run k = 3 replicates in fresh databases
  with arms C / V / N, enforce the hard budget cap, and write results judged ONLY by the fixed Bar.
Owns: the command line (live / dry-run / recorded replay; dev coverage mode), the fresh database per replicate, the
  replicate loop with no reruns, the infrastructure-abort record, the results writer (trials, summary, safety, Bar,
  reported-only metrics, the too-good-to-be-true audit) and the run folder.
Public entry: main(), parse_args(), run_cli(), run_experiment(), build_results(), fresh_env(), K
Decisions: D-0016, D-0021, D-0022, D-0025, D-0006
Assumptions: A-0034, A-0036
Notes: EVALUATION HARNESS ONLY (docs/experiments/EXP-0004-recall-under-interference.md).
    python -m nacre.eval.run_exp0004 --live --tau T          # owner; OPENAI_API_KEY exported in the shell
    python -m nacre.eval.run_exp0004 --dry-run --tau T       # plumbing only, no key, no model
    python -m nacre.eval.run_exp0004 --recorded RUN_DIR --tau T   # replay a live run's fixtures, zero network
    python -m nacre.eval.run_exp0004 --dev-coverage --split dev (--dry-run | --recorded DIR | --live)
  - tau (`--tau`, tau_strong_q, cosine x 1e4) is REQUIRED and recorded; this runner never selects it. The dev coverage
    mode runs no transfers and writes per-task rows (answerable, coverage at TAU_PROBE, top semantic score) only.
  - Live mode refuses to start while the transfer instrument is not owner-approved (INSTRUMENT_APPROVED) and checks
    only that OPENAI_API_KEY is present in the environment (the SDK reads it; it is never read or printed here).
  - One CappedProvider wraps the live provider for the whole run: BudgetExceeded (or any other exception, or a signal)
    marks the run `aborted` with its reason, and no verdict is given. There is no retry and no resume: a repeat is a
    new run with a new id (EXP-0004 "Reruns").
  - Results are judged by grade_exp0004.evaluate_bar only; the audit is reported beside it, never folded into it.
  - An abort records only the exception's type (or our own SystemExit message), never its text: an error message could
    carry set content into run.json.
  - D1: role passwords for the run database default to "nacre_exp_only" (as run_exp0003); results go outside the repo.
"""
import argparse
import json
import os
import secrets
import signal
import statistics
import tempfile
import uuid
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from nacre.core.db import DbRole, connect
from nacre.eval.audit_exp0004 import dev_test_overlap, prompt_contains_answer
from nacre.eval.cap_exp0004_budget import HARD_CAP_USD, BudgetExceeded, CappedProvider, worst_case_cost
from nacre.eval.grade_exp0004 import SAFETY_METRICS, evaluate_bar, summarize
from nacre.eval.load_exp0004_set import DEV_SHA256, TEST_SHA256, load_split
from nacre.eval.provide_exp0004_models import DryProvider, LiveMode, RecordedMode
from nacre.eval.run_exp0004_replicate import TAU_PROBE, Env, run_replicate
from nacre.eval.transfer_exp0004 import INSTRUMENT_APPROVED, INSTRUMENT_SHA256, TRANSFER_MODEL, TRANSFER_PARAMS
from nacre.keys.local_file_root_key import LocalFileRootKeyProvider
from nacre.models.call_model import load_prices
from nacre.models.set_model_policy import set_model_policy
from nacre.schema.apply_migrations import apply_migrations
from nacre.scopes.bootstrap_org import bootstrap_org
from nacre.scopes.open_scoped_session import open_scoped_session

K = 3
ROOT = Path(__file__).resolve().parents[3]
SET_DIR = ROOT / "tests" / "regression" / "exp0004"
SPLITS = {"test": ("test.json", TEST_SHA256), "dev": ("dev.json", DEV_SHA256)}


@contextmanager
def fresh_env(admin_dsn: str, *, role_password: str = "nacre_exp_only", keep: bool = False):
    """A new, migrated database with a bootstrapped org whose model policy allows only the pinned model."""
    name = f"nacre_exp0004_{uuid.uuid4().hex[:10]}"
    dsn = lambda **kw: make_conninfo(**(conninfo_to_dict(admin_dsn) | kw))  # noqa: E731
    try:
        with psycopg.connect(admin_dsn, autocommit=True) as c:
            c.execute(f'CREATE DATABASE "{name}"')
        with connect(DbRole.MIGRATOR, dsn=dsn(dbname=name)) as conn:
            apply_migrations(conn)
        with psycopg.connect(dsn(dbname=name), autocommit=True) as c:
            for role in ("nacre_app", "nacre_keyadmin"):
                c.execute(f"ALTER ROLE {role} LOGIN PASSWORD '{role_password}'")
        app = dsn(dbname=name, user="nacre_app", password=role_password)
        keyadmin = dsn(dbname=name, user="nacre_keyadmin", password=role_password)
        kp = LocalFileRootKeyProvider.initialise(Path(tempfile.mkdtemp()) / "rootkeys")
        owner = uuid.uuid4()
        with connect(DbRole.MIGRATOR, dsn=dsn(dbname=name)) as admin:
            org = bootstrap_org(admin, kp, owner_principal_id=owner, idempotency_key=str(uuid.uuid4()))

        @contextmanager
        def open_as(principal):
            with connect(DbRole.APP, dsn=app) as conn, open_scoped_session(conn, principal) as s:
                yield s
        with open_as(owner) as s:
            set_model_policy(s, kp, org_id=org, allowed=[TRANSFER_MODEL], idempotency_key=str(uuid.uuid4()))
        yield Env(org, owner, kp, open_as, lambda: connect(DbRole.APP, dsn=app), lambda: psycopg.connect(keyadmin))
    finally:
        if not keep:
            with psycopg.connect(admin_dsn, autocommit=True) as c:
                c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _pct(values: list[int], q: float) -> int | None:
    return None if not values else sorted(values)[min(len(values) - 1, int(q * len(values)))]


def build_results(replicates: list, *, aborted: bool, audit: dict) -> dict:
    """Summary, safety, Bar verdict and reported-only metrics of the completed replicates."""
    trials = [t for r in replicates for t in r.trials]
    summary = summarize(trials)
    frames = sum((r.frames for r in replicates), start=Counter())
    safety = {"injection_followed": summary["N"]["injection"], "ungranted_frame_item": frames["ungranted_frame_item"],
              "erased_content": frames["erased_content"] + summary["N"]["erased"],
              "superseded_item": frames["superseded_item"], "untraced_frame": frames["untraced_frame"],
              "replay_mismatch": frames["replay_mismatch"]}
    assert set(safety) == set(SAFETY_METRICS)
    n_rows = [t for t in trials if t["arm"] == "N"]
    per_arm_cost = {a: str(sum((Decimal(t["cost_usd"]) for t in trials if t["arm"] == a), Decimal(0)))
                    for a in ("C", "V", "N")}
    reported = {
        "coverage": {k[9:]: v for k, v in frames.items() if k.startswith("coverage_")},
        "recall_ms": {w: {"p50": _pct(xs, 0.5), "p95": _pct(xs, 0.95)} for w, xs in (
            ("cold", [t["recall_ms"] for t in n_rows if t["recall_cold"]]),
            ("warm", [t["recall_ms"] for t in n_rows if not t["recall_cold"]]))},
        "frame_recall_at_budget": sum(t["target_in_frame"] for t in n_rows),
        "frame_recall_and_success": sum(t["target_in_frame"] and t["success"] for t in n_rows),
        "transfer_cost_usd_per_arm": per_arm_cost,
        "mean_tokens_per_task": {a: statistics.fmean([t["input_tokens"] + t["output_tokens"] for t in trials
                                                      if t["arm"] == a] or [0]) for a in ("C", "V", "N")},
        "sleep": dict(sum((r.sleep for r in replicates), start=Counter())),
        "replay_items": dict(sum((r.replay_items for r in replicates), start=Counter())),
        "frame_items": {k: frames[k] for k in ("frames", "items", "episode_items", "non_authoritative_item")},
        "dropped_event_addresses": sum(r.dropped_addresses for r in replicates)}
    audit = audit | {"non_authoritative_frame_items": frames["non_authoritative_item"],
                     "prompts_identical_apart_from_memory": all(r.prompts_identical for r in replicates)}
    audit["clean"] = (audit["prompt_contains_answer"] == 0 and audit["non_authoritative_frame_items"] == 0
                      and audit["prompts_identical_apart_from_memory"] and audit["dev_test"]["shared_scope_ids"] == 0
                      and audit["dev_test"]["shared_texts"] == 0)
    return {"summary": summary, "safety": safety, "bar": evaluate_bar(summary, safety, aborted=aborted),
            "audit": audit, "reported_not_gated": reported}


def run_experiment(scopes, grading, mode, make_env, *, tau_strong_q: int, embedder, cache_factory, k: int = K,
                   transfers: bool = True, record: dict, save=lambda: None, should_abort=lambda: None) -> list:
    """k replicates, each in a fresh database from `make_env()`; never retried. Aborts are recorded in `record`."""
    replicates = []
    for rep in range(1, k + 1):
        should_abort()
        run_id = uuid.uuid4()
        record["runs"].append({"rep": rep, "run_id": str(run_id), "status": "running"})
        save()
        try:
            with make_env() as env:
                replicates.append(run_replicate(env, scopes, grading, mode, rep=rep, tau_strong_q=tau_strong_q,
                                                embedder=embedder, cache=cache_factory(env), run_id=run_id,
                                                transfers=transfers, should_abort=should_abort))
        except BaseException as exc:
            reason = ("budget cap (infrastructure abort)" if isinstance(exc, BudgetExceeded)
                      else f"SystemExit: {exc}" if isinstance(exc, SystemExit) else type(exc).__name__)
            record["runs"][-1]["status"] = record["status"] = "aborted"
            record["abort_reason"] = record.get("abort_reason") or reason
            save()
            raise
        record["runs"][-1]["status"] = "complete"
        save()
    return replicates


class _Meter:
    """Dry-run meter: counts calls per seat and sums each request's worst-case cost (an upper bound per call)."""

    def __init__(self, inner):
        self.name, self.replay, self._inner, self._prices = inner.name, False, inner, load_prices()
        self.calls, self.upper_usd = {}, Decimal(0)

    def complete(self, request, *, timeout_s):
        self.calls[request.purpose] = self.calls.get(request.purpose, 0) + 1
        self.upper_usd += worst_case_cost(request, self._prices)
        return self._inner.complete(request, timeout_s=timeout_s)


def _admin_dsn() -> str:
    if os.environ.get("NACRE_TEST_DSN"):
        return os.environ["NACRE_TEST_DSN"]
    compose = (ROOT / "docker-compose.yml").read_text()
    password = next(line.split(":", 1)[1].strip() for line in compose.splitlines() if "POSTGRES_PASSWORD:" in line)
    return make_conninfo(host="127.0.0.1", port=54329, user="postgres", password=password, dbname="postgres")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="run_exp0004")
    m = ap.add_mutually_exclusive_group(required=True)
    m.add_argument("--live", action="store_true")
    m.add_argument("--dry-run", action="store_true")
    m.add_argument("--recorded", type=Path)
    ap.add_argument("--split", choices=sorted(SPLITS), default="test")
    ap.add_argument("--tau", type=int)
    ap.add_argument("--dev-coverage", action="store_true")
    ap.add_argument("--out", type=Path, default=Path.home() / "Desktop" / "nacre-runs")
    ap.add_argument("--keep-databases", action="store_true")
    a = ap.parse_args(argv)
    if a.dev_coverage and (a.split != "dev" or a.tau is not None):
        raise SystemExit("--dev-coverage runs on --split dev only and takes no --tau (it selects nothing)")
    if not a.dev_coverage and a.tau is None:
        raise SystemExit("--tau is required: it is fixed on the dev split by the owner, never by this runner")
    if a.live and not INSTRUMENT_APPROVED:
        raise SystemExit("the transfer instrument is a DRAFT (INSTRUMENT_APPROVED = False): no live run")
    if a.live and not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is not set in this shell (export it yourself; it is never printed)")
    return a


def main(argv: list[str] | None = None) -> dict:
    """Verify both frozen splits (sha256), then run the requested split."""
    a = parse_args(argv)
    name, sha = SPLITS[a.split]
    scopes, grading = load_split(SET_DIR / name, sha)
    other, other_sha = SPLITS["dev" if a.split == "test" else "test"]
    other_scopes, _ = load_split(SET_DIR / other, other_sha)
    return run_cli(a, scopes, grading, other_scopes)


def run_cli(a: argparse.Namespace, scopes, grading, other_scopes, *, make_env=None, embedder=None) -> dict:
    """The run for parsed arguments `a` over already-verified data (tests pass synthetic data, an env factory and
    an embedder; the owner's run uses the frozen files, fresh databases and the default embedder)."""
    audit = {"prompt_contains_answer": prompt_contains_answer(grading, {t.task_id: t.prompt for s in scopes
                                                                         for t in s.tasks}),
             "dev_test": dev_test_overlap(*((scopes, other_scopes) if a.split == "dev" else (other_scopes, scopes)))}
    label = "recorded" if a.recorded else "dry" if a.dry_run else "live"
    if a.recorded:
        prev = json.loads((a.recorded / "run.json").read_text())
        if (prev["instrument_sha256"], prev["tau_strong_q"], prev["split"]) != (INSTRUMENT_SHA256, a.tau, a.split):
            raise SystemExit("the recorded run used another instrument, tau or split: not a replay of it")
    rdir = a.out / f"EXP-0004-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{secrets.token_hex(3)}-{label.upper()}"
    rdir.mkdir(parents=True)
    meter = _Meter(DryProvider()) if a.dry_run else None
    capped = None
    if a.live:
        from nacre.models.openai_responses_provider import OpenAIResponsesProvider
        capped = CappedProvider(OpenAIResponsesProvider())
    mode = RecordedMode(a.recorded / "fixtures") if a.recorded else LiveMode(capped or meter, rdir / "fixtures")
    record = {"experiment": "EXP-0004", "mode": label, "split": a.split, "k": K, "tau_strong_q": a.tau,
              "dev_coverage": a.dev_coverage, "instrument_sha256": INSTRUMENT_SHA256,
              "instrument_approved": INSTRUMENT_APPROVED, "transfer_params": repr(TRANSFER_PARAMS),
              "budget_cap_usd": str(HARD_CAP_USD), "status": "running", "runs": []}
    save = lambda: (rdir / "run.json").write_text(json.dumps(record, indent=1, default=str))  # noqa: E731
    stop: dict = {}

    def on_signal(signum, _frame):
        stop.setdefault("reason", f"signal {signal.Signals(signum).name}")

    def should_abort():
        if stop:
            raise SystemExit(stop["reason"])
    previous = {sig: signal.signal(sig, on_signal) for sig in (signal.SIGINT, signal.SIGTERM)}
    from nacre.recall.index_version import default_embedder
    from nacre.recall.load_index_cache import IndexCache
    embedder = embedder or default_embedder()
    try:
        reps = run_experiment(scopes, grading, mode,
                              make_env or (lambda: fresh_env(_admin_dsn(), keep=a.keep_databases)),
                              tau_strong_q=TAU_PROBE if a.dev_coverage else a.tau,
                              embedder=embedder, cache_factory=lambda env: IndexCache(env.key_provider, dim=embedder.dim),
                              transfers=not a.dev_coverage, record=record, save=save, should_abort=should_abort)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        record["spent_usd"] = str(capped.spent) if capped else None
        if meter:
            record["dry_meter"] = {"calls": meter.calls, "worst_case_usd_upper_bound": str(meter.upper_usd)}
        save()
    rows = [row for r in reps for row in r.coverage_rows]
    (rdir / "coverage_rows.json").write_text(json.dumps(rows, indent=1))
    out = {"coverage_rows": len(rows)}
    if not a.dev_coverage:
        (rdir / "trials.json").write_text(json.dumps([t for r in reps for t in r.trials], indent=1))
        out = build_results(reps, aborted=False, audit=audit)
        (rdir / "summary.json").write_text(json.dumps(out, indent=1, default=str))
    record["status"] = "complete"
    save()
    print(f"run folder: {rdir}", flush=True)
    return out


if __name__ == "__main__":
    main()
