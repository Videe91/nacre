"""
Functionality: Run ONE EXP-0004 replicate in one fresh database: every scope's history through capture -> gate ->
  sleep pass after each day, erase_person before the tasks, then arms C / V / N on every task, graded, with the
  N-arm frame checks and a replay of every frame.
Owns: the replicate's order of work, scope registration and grants (each scope's principal is granted only the scopes
  the set lists, so cross-scope twins are unreachable), the erasure step, the per-task arm calls and their sources, the
  trial rows, and the per-replicate safety and audit counters.
Public entry: run_replicate(), Env, ReplicateResult, TAU_PROBE, SCOPE_LEVEL, N_BUDGET, DEV_COVERAGE_BUDGET
Decisions: D-0025, D-0016, D-0018, D-0020, D-0021, D-0023, D-0014
Assumptions: A-0034, A-0036, A-0038
Notes: EVALUATION HARNESS ONLY. Only the arm view reaches an arm; grading fields are read after an arm answered.
  - Capture is written by the run owner's session with actor = the dataset author (capture_exp0004_day). Sleep pass =
    run_sleep_pass (it runs the D-0019 gate first) after each day of each scope.
  - Erasure: the real path, request_shred(ERASE_PERSON) by the org admin, then execute_due_shreds at request time +
    GRACE + 1 s (D1: the 7-day grace is simulated by passing `now`, as the key tests do; no clock is changed).
  - Arms, per task, in the order C, V, N, all through transfer_exp0004 with the same instrument:
    C: "(none)". V: naive_memory_arm over the principal's granted scopes, built once per scope after erasure.
    N: recall_context as the scope's principal (scopes = the granted scopes at level SCOPE_LEVEL, query = the task
    prompt, addresses = the task's addresses; budget 10 items / 4,000 chars) -> render_memory_section.
  - call_model sources (D-0022/D-0023): V's pasted events, N's frame items; with no stream content (C, an empty
    memory) the scope's anchor = its first captured event under the stream key (agent- or system-authored), so the
    recording is keyed but never erased with a person.
  - TAU_PROBE (dev coverage mode only): below any quantised cosine, so `coverage` reports whether the top item meets
    the channel-agreement condition; strong at a given tau = that AND top_semantic >= tau. It selects nothing
    (select_exp0004_tau does, on the rows). D1: the dev coverage mode (no transfers) recalls with DEV_COVERAGE_BUDGET
    (N's 10 items, NO character limit) so the frame's first item is always the ranked top when the probe says strong;
    under N_BUDGET a top item longer than 4,000 chars is skipped and items[0] would be another item, making the
    reduction inexact. Coverage itself never depends on the budget.
  - Latency: recall_ms is end to end (snapshot, frame, trace commit); `recall_cold` marks the first recall of a scope in
    its replicate (its index entries load into the cache then); every later one is warm (D1).
  - The replicate never retries a failed step: any exception (including BudgetExceeded) propagates to the runner.
"""
import sys
import uuid
from collections import Counter
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from time import perf_counter
from uuid import UUID

import psycopg

from nacre.core.embedder import Embedder
from nacre.core.root_key_provider import RootKeyProvider
from nacre.eval.audit_exp0004 import check_frame, prompts_differ_only_in_memory, replay_check
from nacre.eval.capture_exp0004_day import IdMap, capture_day
from nacre.eval.grade_exp0004 import grade_trial
from nacre.eval.load_exp0004_set import ArmScope, GradingTask
from nacre.eval.naive_memory_arm import build_naive_index, render_naive_section
from nacre.eval.transfer_exp0004 import NO_MEMORY, transfer
from nacre.interface.render_frame import render_memory_section
from nacre.keys.execute_due_shreds import execute_due_shreds
from nacre.keys.keyadmin_session import keyadmin_transaction
from nacre.keys.manage_shred_requests import GRACE, ShredKind, request_shred
from nacre.models.call_model import DEFAULT_POLICY
from nacre.recall.assemble_frame import Budget
from nacre.recall.load_index_cache import IndexCache
from nacre.recall.recall_context import RecallRequest, recall_context
from nacre.scopes.open_scoped_session import ScopedSession
from nacre.scopes.register_scope import ScopeKind, register_scope
from nacre.scopes.set_access import set_access
from nacre.sleep.run_sleep_pass import run_sleep_pass

TAU_PROBE = -10001
SCOPE_LEVEL = "project"
N_BUDGET = Budget(items=10, chars=4000)
DEV_COVERAGE_BUDGET = Budget(items=N_BUDGET.items, chars=sys.maxsize)


@dataclass
class Env:
    org_id: UUID
    owner: UUID
    key_provider: RootKeyProvider
    open_as: Callable[[UUID], AbstractContextManager[ScopedSession]]
    app_connect: Callable[[], AbstractContextManager[psycopg.Connection]]
    keyadmin_connect: Callable[[], psycopg.Connection]


@dataclass
class ReplicateResult:
    run_id: UUID
    trials: list[dict] = field(default_factory=list)
    frames: Counter = field(default_factory=Counter)
    replay_items: Counter = field(default_factory=Counter)
    coverage_rows: list[dict] = field(default_factory=list)
    replays: list[tuple[UUID, UUID, int]] = field(default_factory=list)
    prompts_identical: bool = True
    sleep: Counter = field(default_factory=Counter)
    dropped_addresses: int = 0
    fixtures: int = 0


def _grant(env: Env, stream: UUID, principal: UUID, append: bool) -> None:
    with env.open_as(env.owner) as s:
        set_access(s, env.key_provider, org_id=env.org_id, principal_id=principal, stream_id=stream, can_read=True,
                   can_append=append, idempotency_key=str(uuid.uuid4()))


def _anchor(day: tuple, ids: IdMap) -> UUID:
    return next(ids.events[e["event_id"]] for ep in day for e in ep if e["actor_kind"] != "person")


def _history(env: Env, sc: ArmScope, ids: IdMap, mode, rep: int, run_id: UUID, policy, res: ReplicateResult) -> UUID:
    stream, owner_session, anchor = ids.stream(sc.scope_id), (lambda: env.open_as(env.owner)), None
    for d, day in enumerate(sc.days):
        with owner_session() as s:
            capture_day(s, env.key_provider, stream, day, ids, cycle_id=run_id)
        if d == 0:
            anchor = _anchor(day, ids)
            res.fixtures += mode.prepare(owner_session, env.key_provider, stream, rep, sc.scope_id, anchor)
        rep_ = run_sleep_pass(owner_session, env.key_provider, mode.sleep_provider, stream, policy=policy)
        res.sleep.update(episodes=rep_.episodes, calls_live=rep_.calls_live, calls_reused=rep_.calls_reused,
                         promoted=rep_.promoted, refused=rep_.refused, fallback=rep_.fallback,
                         skipped=rep_.skipped, unlinked_action_outcomes=rep_.unlinked_action_outcomes)
        res.sleep["cost_usd_micro"] += int(rep_.cost_usd * 1_000_000)
    return anchor


def _erase(env: Env, scopes: list[ArmScope], ids: IdMap) -> int:
    persons = [p for sc in scopes for p in sc.erase_persons]
    t0 = datetime.now(UTC)
    for p in persons:
        with env.keyadmin_connect() as c, keyadmin_transaction(c) as tx:
            request_shred(tx, env.key_provider, org_id=env.org_id, requester=env.owner, kind=ShredKind.ERASE_PERSON,
                          person_id=ids.actor(p), idempotency_key=str(uuid.uuid4()), now=t0)
    if persons:
        with env.keyadmin_connect() as c:
            execute_due_shreds(c, env.key_provider, now=t0 + GRACE + timedelta(seconds=1))
    return len(persons)


def _recall(env, principal, stream, granted, task, cache, embedder, tau, budget):
    req = RecallRequest(stream, tuple((SCOPE_LEVEL, g) for g in granted), task.prompt, task.addresses)
    t = perf_counter()
    with env.app_connect() as conn:
        r = recall_context(conn, env.key_provider, principal, req, cache=cache, embedder=embedder, tau_strong_q=tau,
                           config_version=f"exp0004-tau-{tau}", budget=budget)
    return r, int((perf_counter() - t) * 1000)


def _tasks(env, sc, ids, anchor, mode, grading, cache, embedder, tau, run_id, rep, policy, transfers, res):
    principal, stream = ids.actor(sc.principal), ids.stream(sc.scope_id)
    granted = [ids.stream(g) for g in sc.granted_scopes]
    if granted != [stream]:
        raise ValueError(f"{sc.scope_id}: one prompt, one scope (D-0022): a principal must be granted its own scope only")
    owner_session = lambda: env.open_as(env.owner)  # noqa: E731
    provider = mode.transfer_provider(owner_session, env.key_provider, stream)
    with env.open_as(principal) as s:
        naive = build_naive_index(s, env.key_provider, granted, embedder)
    for n, task in enumerate(sc.tasks):
        g: GradingTask = grading[task.task_id]
        r, latency = _recall(env, principal, stream, granted, task, cache, embedder, tau,
                             N_BUDGET if transfers else DEV_COVERAGE_BUDGET)
        items = r.frame.body["items"]
        top = items[0]["scores"]["semantic"] if items else None
        res.coverage_rows.append({"rep": rep, "task_id": task.task_id, "category": g.category,
                                  "answerable": not g.expected_ask, "coverage": r.coverage, "top_semantic": top,
                                  "items": len(items)})
        targets = [ids.events[e] for k in ("target_event_ids", "v2_event_ids") for e in g.refs.get(k) or ()
                   if e in ids.events]
        with owner_session() as s:
            fc = check_frame(s, env.key_provider, r.frame.body, granted=set(granted), trace_stream=r.trace_stream,
                             trace_commit_seq=r.trace_commit_seq, frame_id=r.frame.frame_id,
                             erased_regex=g.regexes["erased_regex"], target_event_ids=targets)
        res.frames.update(fc)
        res.frames["frames"] += 1
        res.frames[f"coverage_{r.coverage}"] += 1
        res.replays.append((principal, r.trace_stream, r.trace_commit_seq))
        if not transfers:
            continue
        hits = naive.top(task.prompt)
        memories = {"C": (NO_MEMORY, [anchor]),
                    "V": (render_naive_section(hits), [h.event_id for h in hits] or [anchor]),
                    "N": (render_memory_section(r.frame.body),
                          [UUID(i["version_event_id"]) for i in items] or [anchor])}
        sent = {}
        for arm, (memory, sources) in memories.items():
            t = transfer(owner_session, env.key_provider, provider, arm=arm, task=task.prompt, memory_section=memory,
                         sources=sources, run_id=run_id, policy=policy,
                         frame_id=r.frame.frame_id if arm == "N" else None)       # D-0022 am.1: N only
            sent[arm] = (t.prompt, memory)
            row = grade_trial(g, t.reply) | {"arm": arm, "rep": rep, "run_id": str(run_id), "cost_usd": str(t.cost_usd),
                                             "input_tokens": t.input_tokens, "output_tokens": t.output_tokens,
                                             "memory_items": len(hits) if arm == "V" else len(items) if arm == "N" else 0}
            if arm == "N":
                row |= {"coverage": r.coverage, "frame_id": r.frame.frame_id, "recall_ms": latency,
                        "recall_cold": n == 0, "target_in_frame": bool(fc["target_in_frame"])}
            res.trials.append(row)
        res.prompts_identical &= prompts_differ_only_in_memory(sent)


def run_replicate(env: Env, scopes: list[ArmScope], grading: dict[str, GradingTask], mode, *, rep: int,
                  tau_strong_q: int, embedder: Embedder, cache: IndexCache, run_id: UUID | None = None, policy=None,
                  transfers: bool = True, should_abort: Callable[[], None] = lambda: None) -> ReplicateResult:
    """One replicate over `scopes` (arm view) in `env` (a fresh database)."""
    policy = policy or DEFAULT_POLICY
    run_id = run_id or uuid.uuid4()
    ids, res = IdMap(run_id), ReplicateResult(run_id)
    for sc in scopes:
        stream = ids.stream(sc.scope_id)
        with env.open_as(env.owner) as s:
            register_scope(s, env.key_provider, org_id=env.org_id, stream_id=stream, kind=ScopeKind.PROJECT,
                           idempotency_key=str(uuid.uuid4()))
        _grant(env, stream, env.owner, True)
    for sc in scopes:
        for g in sc.granted_scopes:
            _grant(env, ids.stream(g), ids.actor(sc.principal), True)
    anchors = {}
    for sc in scopes:
        should_abort()
        anchors[sc.scope_id] = _history(env, sc, ids, mode, rep, run_id, policy, res)
    for sc in scopes:                                    # before erasure: the history recordings are still readable
        res.fixtures += mode.checkpoint(lambda: env.open_as(env.owner), env.key_provider, ids.stream(sc.scope_id), rep,
                                        sc.scope_id)
    res.sleep["erased_persons"] = _erase(env, scopes, ids)
    for sc in scopes:
        should_abort()
        _tasks(env, sc, ids, anchors[sc.scope_id], mode, grading, cache, embedder, tau_strong_q, run_id, rep, policy,
               transfers, res)
    for principal, stream, seq in res.replays:
        with env.app_connect() as conn:
            rc = replay_check(conn, env.key_provider, principal, stream, seq, cache=cache, embedder=embedder)
        res.frames["replay_mismatch"] += rc["mismatch"]
        res.frames[f"replay_{rc['status']}"] += 1
        res.replay_items.update(rc["items"])
    for sc in scopes:
        res.fixtures += mode.finish(lambda: env.open_as(env.owner), env.key_provider, ids.stream(sc.scope_id), rep,
                                    sc.scope_id)
    res.dropped_addresses = ids.dropped_addresses
    return res
