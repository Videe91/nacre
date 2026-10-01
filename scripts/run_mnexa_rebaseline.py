"""
EXP-0001 (pre-registered in docs/experiments/EXP-0001-mnexa-rebaseline.md): a fresh MNEXA baseline and a
no-memory control on the pinned model, k = 3, sets 014-016. Decision: D-0016.

Run it with the MNEXA environment's Python (it imports MNEXA, not Nacre):
    ~/Desktop/nacre-runs/mnexa-venv/bin/python scripts/run_mnexa_rebaseline.py run        # owner, with a key
    ~/Desktop/nacre-runs/mnexa-venv/bin/python scripts/run_mnexa_rebaseline.py run --dry-run   # plumbing, no key
    ~/Desktop/nacre-runs/mnexa-venv/bin/python scripts/run_mnexa_rebaseline.py summarize RUN_DIR

Rules it enforces:
- The API key is read from OPENAI_API_KEY by the OpenAI SDK. This script never reads, prints or writes it.
- MNEXA runs from a COPY (`.env*`, results, caches and sqlite files are never copied); the MNEXA repo is not written.
- tasks_014/015/016 must match the sha256 in tests/regression/mnexa/MANIFEST.json, or it refuses.
- Model is a dated pin. Embedder is loaded offline at a fixed revision.
- The harness is MNEXA's unmodified main(). The only addition wraps the OpenAI client's responses.create to log
  {prompt, prompt_sha256, output_text, response_id, model, usage}; the response object is returned unchanged.
- Any step failure marks the run `aborted` and stops (pre-registered rule: never discard a run for its results).
"""
import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

NACRE = Path(__file__).resolve().parent.parent
MNEXA = Path.home() / "Desktop" / "mnexa"
MANIFEST = NACRE / "tests" / "regression" / "mnexa" / "MANIFEST.json"
MODEL = "gpt-4o-mini-2024-07-18"
EMBED_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
SETS = ("014", "015", "016")
K = 3
CONDITION = {"014": "lossless", "015": "verbose", "016": "verbose"}
NEVER_COPY = re.compile(r"(^|/)(\.env[^/]*|__pycache__|\.git)(/|$)|^experiments/results/|\.sqlite3?$")

LAUNCHER = r'''
import hashlib, json, os, sys, time
sys.path.insert(0, os.getcwd())
LOG = os.environ["NACRE_CALL_LOG"]
DRY = os.environ.get("NACRE_DRY_RUN") == "1"
import model_adapter

def _log(rec):
    with open(LOG, "a") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")

if DRY:
    class _Fake:
        def __init__(self, model): self.name = model
        def generate(self, prompt):
            text = "DRY-RUN: no model was called."
            _log({"prompt": prompt, "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(), "output_text": text,
                  "response_id": None, "model": "dry-run", "usage": None, "t": time.time()})
            return model_adapter.Generation(text=text, input_tokens=None, output_tokens=None, response_id=None)
    model_adapter.OpenAIResponsesModel = _Fake
else:
    _orig_init = model_adapter.OpenAIResponsesModel.__init__
    def _init(self, model):
        _orig_init(self, model)
        create = self._client.responses.create
        def logged(**kw):
            t0 = time.time()
            r = create(**kw)
            u = getattr(r, "usage", None)
            p = kw.get("input")
            _log({"prompt": p, "prompt_sha256": hashlib.sha256(str(p).encode()).hexdigest(),
                  "output_text": r.output_text, "response_id": getattr(r, "id", None),
                  "model": getattr(r, "model", None), "request_model": kw.get("model"),
                  "usage": {"input_tokens": getattr(u, "input_tokens", None),
                            "output_tokens": getattr(u, "output_tokens", None)} if u else None,
                  "latency_s": round(time.time() - t0, 3), "t": t0})
            return r
        self._client.responses.create = logged
    model_adapter.OpenAIResponsesModel.__init__ = _init

mode, set_id, out = sys.argv[1], sys.argv[2], sys.argv[3]
tasks = f"experiments/tasks_{set_id}.json"
if mode == "harness":
    import importlib
    mod = importlib.import_module(f"experiments.seed_growth_{set_id}")
    if DRY:
        print(json.dumps({"dry_run": True, "imported": mod.__name__}))
        sys.exit(0)
    sys.argv = [mod.__name__, "--tasks", tasks, "--results", out]
    mod.main()
elif mode == "control":
    from pathlib import Path
    from mnexa_seed import MnexaSeed
    from experiments.seed_growth_004 import make_fidelity_reasoner
    from experiments.seed_growth_013 import semantic_grade
    model = model_adapter.OpenAIResponsesModel(os.environ["MNEXA_MODEL"])
    embedder = model_adapter.SentenceTransformerEmbedder(os.environ["MNEXA_EMBED_MODEL"])
    meter = model_adapter.WordMeter()
    attempts = 3 if set_id == "016" else 1
    families = json.loads(Path(tasks).read_text())["families"]
    rows = []
    Path(out).mkdir(parents=True, exist_ok=True)
    for fam in families:
        passes, decisions = 0, []
        for a in range(attempts):
            db = Path(out) / f"{fam['id']}_a{a}.sqlite3"
            seed = MnexaSeed(db, embedder=embedder, meter=meter)
            try:
                d = seed.decide(fam["transfer"]["prompt"], tuple(fam["entities"]), make_fidelity_reasoner(model))
            finally:
                seed.close()
            g = semantic_grade(d.text, fam["semantic_grader"])
            passes += bool(g["passed"])
            decisions.append({"decision": d.text, "passed": bool(g["passed"]), "memory_segments": list(d.memory_segments)})
        rows.append({"family_id": fam["id"], "attempts": attempts, "passes": passes,
                     "pass": passes > attempts / 2, "decisions": decisions})
    (Path(out) / "control.json").write_text(json.dumps({"set": set_id, "families": rows}, indent=1))
    print(json.dumps({"set": set_id, "control_passes": sum(r["pass"] for r in rows), "families": len(rows)}))
'''


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def snapshot_mnexa(dest: Path) -> dict:
    names = subprocess.run(["git", "-C", str(MNEXA), "ls-files", "-c", "-o", "--exclude-standard", "-z"],
                           capture_output=True, check=True).stdout.decode().split("\0")
    files = {}
    for name in sorted(n for n in names if n and not NEVER_COPY.search(n)):
        src = MNEXA / name
        if src.is_file():
            (dest / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / name)
            files[name] = sha(src)
    frozen = json.loads(MANIFEST.read_text())["task_sets"]
    for s in SETS:
        if files.get(f"experiments/tasks_{s}.json") != frozen[f"tasks_{s}"]["sha256"]:
            raise SystemExit(f"tasks_{s}.json does not match the frozen manifest; refusing to run")
    head = subprocess.run(["git", "-C", str(MNEXA), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    tree = hashlib.sha256("".join(f"{n}\0{h}\n" for n, h in files.items()).encode()).hexdigest()
    return {"mnexa_head": head, "source_files": len(files), "source_tree_sha256": tree, "files": files}


def run(out_root: Path, dry: bool) -> Path:
    if not dry and not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is not set in this shell (export it yourself; it is never printed).")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    rdir = out_root / f"EXP-0001-{stamp}-{secrets.token_hex(3)}{'-DRYRUN' if dry else ''}"
    src = rdir / "src"
    src.mkdir(parents=True)
    record = {"experiment": "EXP-0001", "dry_run": dry, "started_utc": stamp, "model": MODEL,
              "embedder": f"sentence-transformers/all-MiniLM-L6-v2@{EMBED_REVISION}", "k": K, "sets": SETS,
              "nacre_head": subprocess.run(["git", "-C", str(NACRE), "rev-parse", "HEAD"], capture_output=True,
                                           text=True).stdout.strip(),
              "python": sys.version.split()[0], "steps": [], "status": "running"}
    record["source"] = snapshot_mnexa(src)
    (src / "_nacre_launcher.py").write_text(LAUNCHER)
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH",)}
    env.update(MNEXA_MODEL=MODEL, MNEXA_EMBED_MODEL="sentence-transformers/all-MiniLM-L6-v2", HF_HUB_OFFLINE="1",
               TRANSFORMERS_OFFLINE="1", NACRE_CALL_LOG=str(rdir / "calls.jsonl"), NACRE_DRY_RUN="1" if dry else "0",
               PYTHONDONTWRITEBYTECODE="1")
    save = lambda: (rdir / "run.json").write_text(json.dumps(record, indent=1, sort_keys=True))  # noqa: E731
    save()

    def stop(signum, _frame):
        record["status"] = "aborted"
        record["abort_reason"] = f"signal {signal.Signals(signum).name} (interrupted, terminal closed, or killed)"
        save()
        raise SystemExit(f"run aborted by {signal.Signals(signum).name}; recorded in {rdir}/run.json")
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, stop)
    for rep in range(1, K + 1):
        for s in SETS:
            for mode in ("harness", "control"):
                out = rdir / mode / s / f"rep{rep}"
                out.mkdir(parents=True, exist_ok=True)
                t0 = time.time()
                # Output streams to disk live, so a killed run still shows how far it got and why.
                with open(out / "stdout.txt", "w") as so, open(out / "stderr.txt", "w") as se:
                    p = subprocess.run([sys.executable, "_nacre_launcher.py", mode, s, str(out)], cwd=src, env=env,
                                       stdout=so, stderr=se, text=True, timeout=7200)
                step = {"rep": rep, "set": s, "mode": mode, "exit": p.returncode, "seconds": round(time.time() - t0, 1)}
                record["steps"].append(step)
                save()
                print(json.dumps(step), flush=True)
                if p.returncode != 0:
                    record["status"] = "aborted"
                    save()
                    raise SystemExit(f"step failed ({step}); run marked aborted. See {out}/stderr.txt")
    record["status"] = "complete"
    record["finished_utc"] = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    save()
    return rdir


def summarize(rdir: Path) -> dict:
    record = json.loads((rdir / "run.json").read_text())
    if record["status"] != "complete":
        raise SystemExit(f"run status is {record['status']}; only complete runs are summarised")
    per = {}
    safety = {"unsupported_claims_admitted": 0, "unsafe_support_challenges_admitted": 0,
              "fabricated_support_challenges_admitted": 0, "nonauthoritative_support_challenges_admitted": 0,
              "fallback_ancestry_invalid_families": 0}
    for s in SETS:
        for rep in range(1, K + 1):
            results = sorted((rdir / "harness" / s / f"rep{rep}").glob("*/result.json"))
            if len(results) != 1:
                raise SystemExit(f"expected one result.json for {s} rep{rep}, found {len(results)}")
            fams = json.loads(results[0].read_text())["families"]
            cond = CONDITION[s]
            if s == "016":
                b = sum(bool(f[cond]["summary"]["majority_pass"]) for f in fams)
                safety["unsupported_claims_admitted"] += sum(f[cond]["summary"]["unsupported_claims_admitted"] for f in fams)
            else:
                b = sum(bool(f[cond]["semantic_task_pass"]) for f in fams)
                for f in fams:
                    c = f[cond]
                    safety["unsupported_claims_admitted"] += c.get("unsupported_claims_admitted", 0)
                    for k in ("unsafe_support_challenges_admitted", "fabricated_support_challenges_admitted",
                              "nonauthoritative_support_challenges_admitted"):
                        safety[k] += c.get(k, 0)
                    if c.get("fallback_ancestry_valid") is False:
                        safety["fallback_ancestry_invalid_families"] += 1
            ctrl = json.loads((rdir / "control" / s / f"rep{rep}" / "control.json").read_text())["families"]
            per.setdefault(s, []).append({"rep": rep, "B": b, "C": sum(r["pass"] for r in ctrl), "families": len(fams)})
    B = sum(r["B"] for v in per.values() for r in v)
    C = sum(r["C"] for v in per.values() for r in v)
    n = sum(r["families"] for v in per.values() for r in v)
    calls = [json.loads(line) for line in (rdir / "calls.jsonl").read_text().splitlines()]
    tokens_in = sum((c.get("usage") or {}).get("input_tokens") or 0 for c in calls)
    tokens_out = sum((c.get("usage") or {}).get("output_tokens") or 0 for c in calls)
    summary = {
        "experiment": "EXP-0001", "run_dir": rdir.name, "model": record["model"],
        "models_reported": sorted({str(c.get("model")) for c in calls}),
        "trials_per_arm": n, "B_fresh_mnexa": B, "C_no_memory": C,
        "B_rate": round(B / n, 4), "C_rate": round(C / n, 4),
        "validity_B_minus_C_pp": round(100 * (B - C) / n, 1), "validity_pass": (B - C) * 100 >= 30 * n,
        "per_set": {s: {"B": sum(r["B"] for r in v), "C": sum(r["C"] for r in v), "per_rep": v} for s, v in per.items()},
        "mnexa_safety_totals": safety,
        "calls": len(calls), "input_tokens": tokens_in, "output_tokens": tokens_out,
        "margins_for_nacre": {"non_inferiority_pooled_min": B - 9, "non_inferiority_per_set_min": {
            s: sum(r["B"] for r in v) - 6 for s, v in per.items()},
            "superiority_pooled_min": C + 54, "superiority_per_set_min": {s: sum(r["C"] for r in v) + 9 for s, v in per.items()},
            "safety": "zero on every metric"},
        "source_tree_sha256": record["source"]["source_tree_sha256"],
    }
    (rdir / "summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True))
    return summary


def main(argv):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--out", type=Path, default=Path.home() / "Desktop" / "nacre-runs")
    r.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("summarize")
    s.add_argument("run_dir", type=Path)
    a = ap.parse_args(argv)
    if a.cmd == "run":
        rdir = run(a.out, a.dry_run)
        print(f"run complete: {rdir}")
        if not a.dry_run:
            print(json.dumps(summarize(rdir), indent=1, sort_keys=True))
    else:
        print(json.dumps(summarize(a.run_dir), indent=1, sort_keys=True))


if __name__ == "__main__":
    main(sys.argv[1:])
