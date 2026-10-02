"""
Functionality: Select EXP-0004's coverage threshold tau (tau_strong_q) on the dev split, freeze it as a record, and
  check that freeze before any run that uses a tau; plus its config_event and the EXP-0004 text block.
Owns: the selection rule (max balanced accuracy, ties to the higher tau), the "strong at tau" reduction from the dev
  coverage rows, the frozen record format (TAU.json) and its checks, the `recall_tau` config_event content and append,
  the EXP-0004 text block, and the `select-tau` command line.
Public entry: select_tau(), strong_at(), Selection, TauSelectionError, make_record(), build_record(), check_frozen(),
  config_event_content(), append_tau_config_event(), exp_doc_block(), main(), RECORD_PATH, ANSWERABLE, UNANSWERABLE
Decisions: D-0025, D-0016
Assumptions: A-0034
Notes: EXP-0004 "Fixed before the run (owner, 2026-10-02)" -> "tau (coverage threshold)"; D-0025 §6.
    PYTHONPATH=src .venv/bin/python -m nacre.eval.select_exp0004_tau RUN_DIR [--record PATH]
  - Answerable (owner): T1, T2, T3 not erased. Unanswerable: T5 and erasure targets (category "E"). A row whose
    `answerable` flag disagrees with its category fails loudly (the rule is the owner's, not the data's).
  - strong at tau = (coverage at TAU_PROBE == strong) AND top_semantic >= tau. Exact because every strong condition
    except `semantic >= tau` is tau-independent, TAU_PROBE is below any quantised cosine, and the dev frame's first
    item IS the ranked top whenever the probe says strong (the top is never pruned then, and the dev-coverage budget
    has no character limit: DEV_COVERAGE_BUDGET in the replicate module). Property-tested in
    tests/eval/test_select_exp0004_tau.py against assess_coverage and assemble_frame.
  - D1: candidates = the distinct top_semantic scores of the strong-eligible rows (probe strong); any other tau gives
    the same classification as the next candidate up, except "above every score" (nothing strong), which is not a
    candidate. Rows of all k replicates are pooled, one observation per (rep, task).
  - Fails loudly (TauSelectionError, never a default): no answerable rows, no unanswerable rows, no strong-eligible
    rows, a malformed or duplicate row. Balanced accuracy is an exact Fraction, so ties are exact.
  - The record holds the rows it was selected from; check_frozen recomputes the selection from them, and pins the dev
    split sha256 (record AND the file on disk), this file's sha256, and the tau passed. A live run refuses a record
    selected from a dry dev run. This file never opens test.json.
  - config_event: op `recall_tau` in the org stream of the run database, written by the org owner (the
    set_model_policy pattern, which fresh_env already follows). No floats (D-0008): balanced accuracy as "num/den".
"""
import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from uuid import UUID

from nacre.core.event import ActorKind, EventType, PayloadType, Source
from nacre.core.root_key_provider import RootKeyProvider
from nacre.eval.load_exp0004_set import DEV_SHA256, load_split
from nacre.ledger.append_event import AppendRequest, Authorship, append_event
from nacre.scopes.open_scoped_session import ScopedSession

ROOT = Path(__file__).resolve().parents[3]
SET_DIR = ROOT / "tests" / "regression" / "exp0004"
RECORD_PATH = SET_DIR / "TAU.json"
ANSWERABLE, UNANSWERABLE = frozenset({"T1", "T2", "T3"}), frozenset({"T5", "E"})
RULE = ("tau_strong_q = argmax over the distinct top_semantic scores of the strong-eligible dev rows of the balanced "
        "accuracy of (coverage == strong at tau) against answerable (T1/T2/T3 not erased); ties -> higher tau")


class TauSelectionError(ValueError):
    """tau cannot be selected truthfully from these rows."""


@dataclass(frozen=True)
class Selection:
    tau_strong_q: int
    balanced_accuracy: Fraction
    tp: int
    fn: int
    tn: int
    fp: int
    candidates: int
    rows: int
    strong_eligible: int


def _code_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def strong_at(row: dict, tau: int) -> bool:
    """Coverage would be `strong` at `tau` for this dev row (probe coverage strong AND top semantic >= tau)."""
    return row["coverage"] == "strong" and row["top_semantic"] >= tau


def _validated(rows: list[dict]) -> list[dict]:
    seen = set()
    for r in rows:
        if not isinstance(r, dict) or r.get("category") not in ANSWERABLE | UNANSWERABLE:
            raise TauSelectionError(f"a row without a known category: {r!r}"[:200])
        if r.get("answerable") is not (r["category"] in ANSWERABLE):
            raise TauSelectionError(f"{r.get('task_id')}: `answerable` disagrees with category {r['category']}")
        if r.get("coverage") not in ("strong", "weak", "none"):
            raise TauSelectionError(f"{r.get('task_id')}: coverage {r.get('coverage')!r}")
        top = r.get("top_semantic")
        if r["coverage"] == "strong" and (type(top) is not int or not -10_000 <= top <= 10_000):
            raise TauSelectionError(f"{r.get('task_id')}: a strong row needs an integer top_semantic, not {top!r}")
        key = (r.get("rep"), r.get("task_id"))
        if key in seen:
            raise TauSelectionError(f"duplicate row {key}")
        seen.add(key)
    return rows


def select_tau(rows: list[dict]) -> Selection:
    """The owner's rule over dev coverage rows; deterministic; raises TauSelectionError rather than default."""
    rows = _validated(rows)
    pos = sum(r["category"] in ANSWERABLE for r in rows)
    neg = len(rows) - pos
    if not pos or not neg:
        raise TauSelectionError(f"balanced accuracy is undefined: {pos} answerable, {neg} unanswerable rows")
    eligible = [r for r in rows if r["coverage"] == "strong"]
    if not eligible:
        raise TauSelectionError("no strong-eligible row (coverage at the probe is never strong): no tau to select")
    candidates = sorted({r["top_semantic"] for r in eligible}, reverse=True)
    best = None
    for tau in candidates:                                  # high to low: only a STRICTLY better one replaces
        tp = sum(strong_at(r, tau) for r in eligible if r["category"] in ANSWERABLE)
        fp = sum(strong_at(r, tau) for r in eligible if r["category"] in UNANSWERABLE)
        ba = (Fraction(tp, pos) + Fraction(neg - fp, neg)) / 2
        if best is None or ba > best.balanced_accuracy:
            best = Selection(tau, ba, tp, pos - tp, neg - fp, fp, len(candidates), len(rows), len(eligible))
    return best


def _selection_dict(s: Selection) -> dict:
    d = asdict(s)
    d["balanced_accuracy"] = f"{s.balanced_accuracy.numerator}/{s.balanced_accuracy.denominator}"
    return d


def make_record(rows: list[dict], *, dev_run: dict, split_sha256: str, date: str) -> dict:
    """The frozen record (TAU.json content) for `rows` of the dev run described by `dev_run`."""
    s = select_tau(rows)
    return {"experiment": "EXP-0004", "parameter": "tau_strong_q", "tau_strong_q": s.tau_strong_q, "rule": RULE,
            "selection": _selection_dict(s), "dev_run": dev_run, "dev_split_sha256": split_sha256,
            "selection_code_sha256": _code_sha256(), "selected_on": date, "rows": rows}


def build_record(run_dir: Path, grading: dict, *, split_sha256: str = DEV_SHA256, date: str | None = None) -> dict:
    """The record for a COMPLETE dev-coverage run folder; every dev task must have one row per replicate."""
    run = json.loads((run_dir / "run.json").read_text())
    if (run.get("experiment"), run.get("split"), run.get("dev_coverage"), run.get("status")) != \
            ("EXP-0004", "dev", True, "complete") or any(r["status"] != "complete" for r in run["runs"]):
        raise TauSelectionError(f"{run_dir.name}: not a complete EXP-0004 dev-coverage run")
    if run.get("split_sha256") != split_sha256:
        raise TauSelectionError(f"{run_dir.name}: run on split {run.get('split_sha256')}, not {split_sha256}")
    rows = json.loads((run_dir / "coverage_rows.json").read_text())
    want = {(rep, t) for rep in range(1, len(run["runs"]) + 1) for t in grading}
    if {(r.get("rep"), r.get("task_id")) for r in rows} != want or len(rows) != len(want):
        raise TauSelectionError("the rows are not exactly one per dev task per replicate")
    if any(r["category"] != grading[r["task_id"]].category for r in rows):
        raise TauSelectionError("a row's category disagrees with the dev split's grading view")
    dev_run = {"folder": run_dir.name, "mode": run["mode"], "k": run["k"],
               "run_ids": [r["run_id"] for r in run["runs"]], "embedder_id": run.get("embedder_id"),
               "coverage_rows_sha256": hashlib.sha256((run_dir / "coverage_rows.json").read_bytes()).hexdigest()}
    return make_record(rows, dev_run=dev_run, split_sha256=split_sha256,
                       date=date or datetime.now(UTC).date().isoformat())


def check_frozen(record_path: Path, *, tau: int, live: bool, dev_path: Path = SET_DIR / "dev.json") -> dict:
    """The frozen record for `tau`, or SystemExit naming why a run must not start."""
    if not Path(record_path).is_file():
        raise SystemExit(f"tau is not frozen: no record at {record_path} (run select_exp0004_tau on the dev run)")
    raw = Path(record_path).read_bytes()
    rec = json.loads(raw)
    if rec.get("tau_strong_q") != tau or type(tau) is not int:
        raise SystemExit(f"--tau {tau} does not match the frozen tau_strong_q {rec.get('tau_strong_q')}")
    if rec.get("dev_split_sha256") != DEV_SHA256:
        raise SystemExit("the frozen tau was selected on another dev split (sha256 differs from the pin)")
    if hashlib.sha256(Path(dev_path).read_bytes()).hexdigest() != DEV_SHA256:
        raise SystemExit("dev.json on disk does not match its pinned sha256")
    if rec.get("selection_code_sha256") != _code_sha256():
        raise SystemExit("the selection code changed since tau was frozen: select again on the dev run")
    try:
        again = _selection_dict(select_tau(rec["rows"]))
    except (TauSelectionError, KeyError, TypeError) as exc:
        raise SystemExit(f"the frozen record does not reselect: {type(exc).__name__}") from None
    if again != rec.get("selection") or again["tau_strong_q"] != tau:
        raise SystemExit("the frozen record's selection does not match its own rows")
    if live and rec.get("dev_run", {}).get("mode") not in ("live", "recorded"):
        raise SystemExit("a live run needs tau selected on a live (or recorded) dev run, not a dry one")
    return rec | {"record_sha256": hashlib.sha256(raw).hexdigest()}


def config_event_content(rec: dict) -> dict:
    """The `recall_tau` config_event content for a checked record (from check_frozen)."""
    return {"op": "recall_tau", "experiment": "EXP-0004", "tau_strong_q": rec["tau_strong_q"],
            "balanced_accuracy": rec["selection"]["balanced_accuracy"], "record_sha256": rec["record_sha256"],
            "dev_split_sha256": rec["dev_split_sha256"], "selection_code_sha256": rec["selection_code_sha256"],
            "dev_run_ids": list(rec["dev_run"]["run_ids"]), "selected_on": rec["selected_on"]}


def append_tau_config_event(session: ScopedSession, key_provider: RootKeyProvider, *, org_id: UUID, content: dict,
                            idempotency_key: str) -> UUID:
    """Append the `recall_tau` config_event to the org stream as the session's principal (org owner)."""
    if content.get("op") != "recall_tau" or type(content.get("tau_strong_q")) is not int:
        raise TauSelectionError("not a recall_tau config_event")
    return append_event(session, key_provider, AppendRequest(
        stream_id=org_id, org_id=org_id, event_type=EventType.CONFIG_EVENT, payload_type=PayloadType.STRUCTURED,
        actor_kind=ActorKind.PERSON, actor_id=session.access.principal_id, source=Source.SYSTEM,
        authorship=Authorship.SCOPE_PRINCIPAL, idempotency_key=idempotency_key, content=content)).envelope.event_id


def exp_doc_block(rec: dict, record_sha256: str) -> str:
    """The text block to append to the EXP-0004 file under "tau (coverage threshold)"."""
    s, d = rec["selection"], rec["dev_run"]
    return "\n".join([
        f"#### tau selected and frozen ({rec['selected_on']})",
        f"- **tau_strong_q = {rec['tau_strong_q']}** (cosine x 1e4), selected on the dev split only by "
        f"`src/nacre/eval/select_exp0004_tau.py` (sha256 `{rec['selection_code_sha256']}`).",
        f"- **Balanced accuracy:** {s['balanced_accuracy']} = {float(Fraction(s['balanced_accuracy'])):.4f}; "
        f"TP {s['tp']}, FN {s['fn']}, TN {s['tn']}, FP {s['fp']} over {s['rows']} rows "
        f"({s['strong_eligible']} strong-eligible, {s['candidates']} candidate scores).",
        f"- **Dev run:** `{d['folder']}` ({d['mode']}, k = {d['k']}; run ids {', '.join(d['run_ids'])}); "
        f"rows sha256 `{d['coverage_rows_sha256']}`.",
        f"- **Dev split sha256:** `{rec['dev_split_sha256']}`.",
        f"- **Frozen record:** `tests/regression/exp0004/TAU.json`, sha256 `{record_sha256}`. The test run refuses to "
        "start unless `--tau` equals it and every pin matches; each run database records it as a `recall_tau` "
        "config_event in the org stream."])


def main(argv: list[str] | None = None) -> dict:
    ap = argparse.ArgumentParser(prog="select_exp0004_tau")
    ap.add_argument("run_dir", type=Path, help="a complete --dev-coverage --split dev run folder")
    ap.add_argument("--record", type=Path, default=RECORD_PATH)
    a = ap.parse_args(argv)
    if a.record.exists():
        raise SystemExit(f"{a.record} exists: a frozen tau is never overwritten (remove it in git, deliberately)")
    _, grading = load_split(SET_DIR / "dev.json", DEV_SHA256)           # dev only; test.json is never opened
    try:
        rec = build_record(a.run_dir, grading)
    except TauSelectionError as exc:
        raise SystemExit(f"no tau selected: {exc}") from None
    raw = (json.dumps(rec, indent=1, sort_keys=True) + "\n").encode()
    a.record.write_bytes(raw)
    block = exp_doc_block(rec, hashlib.sha256(raw).hexdigest())
    (a.run_dir / "tau_selection.md").write_text(block + "\n")
    print(block, flush=True)
    return rec


if __name__ == "__main__":
    main()
