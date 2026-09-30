"""
A-0017 evaluation: gitleaks v8.30.1 rules under Python `re` vs `google-re2`.
Not part of the product. Needs an environment with google-re2 and hypothesis (NOT project deps):
    python -m venv /tmp/re2env && /tmp/re2env/bin/pip install google-re2 hypothesis
    /tmp/re2env/bin/python scripts/a0017/eval_regex_engines.py <gitleaks.toml> <corpus_dir> <out.json>
Measures, per rule:
  1. compilation under re (Unicode), re (ASCII) and re2;
  2. match-span agreement with re2 (the engine family gitleaks' Go RE2 syntax targets) on
     a real-text corpus + hypothesis-generated positives;
  3. worst-case single-probe search time on adversarial probes (subprocess, per-probe hard timeout).
"""
import hashlib
import json
import multiprocessing as mp
import re
import sys
import time
import tomllib
import warnings
from pathlib import Path

import re2
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

EXPECTED_SHA256 = "e163e53b9e7e8a8511e77271e2b323ed057759542a6d988258afe3a1fa329caf"
PROBE_LEN = 20_000
TIMEOUT_S = 10.0
SLOW_S = 0.1
ENGINES = ("re", "re_ascii", "re2")


def compile_all(pattern):
    out = {}
    for name in ENGINES:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            try:
                if name == "re":
                    out[name] = (re.compile(pattern), None)
                elif name == "re_ascii":
                    out[name] = (re.compile(pattern, re.ASCII), None)
                else:
                    out[name] = (re2.compile(pattern), None)
            except Exception as exc:  # noqa: BLE001 - we record every failure kind
                out[name] = (None, f"{type(exc).__name__}: {exc}")
        if caught and out[name][0] is not None:
            out[name] = (out[name][0], "warning: " + "; ".join(str(w.message) for w in caught))
    return out


def positives(pattern, n=30):
    found = []

    @settings(max_examples=n, derandomize=True, database=None, deadline=None,
              suppress_health_check=list(HealthCheck))
    @given(st.from_regex(re.compile(pattern), fullmatch=False))
    def collect(s):
        found.append(s)

    try:
        collect()
    except Exception:  # noqa: BLE001 - generation failure is reported as zero positives
        pass
    return found


def spans(compiled, text):
    return [m.span() for m in compiled.finditer(text)]


def probes(rule, pos_examples):
    chars = "aA0f =-_/+.:\"'\nx"
    ps = {f"run[{c!r}]": c * PROBE_LEN for c in chars}
    for kw in rule.get("keywords", [])[:2]:
        ps[f"kw[{kw}]+a"] = (kw + ' = "' + "a" * PROBE_LEN)
        ps[f"kw[{kw}] repeated"] = ((kw + "=") * (PROBE_LEN // (len(kw) + 1)))
    for i, g in enumerate(pos_examples[:3]):
        if len(g) > 1:
            ps[f"near-miss[{i}]"] = ((g[:-1] + " ") * (PROBE_LEN // len(g) + 1))[:PROBE_LEN]
            ps[f"half-prefix[{i}]"] = (g[: len(g) // 2 + 1] * (PROBE_LEN // (len(g) // 2 + 1) + 1))[:PROBE_LEN]
    return ps


def _time_worker(args):
    engine, pattern, probe_items = args
    compiled = re.compile(pattern, re.ASCII) if engine == "re_ascii" else (
        re.compile(pattern) if engine == "re" else re2.compile(pattern))
    worst = (0.0, None)
    for name, text in probe_items:
        t0 = time.perf_counter()
        compiled.search(text)
        dt = time.perf_counter() - t0
        if dt > worst[0]:
            worst = (dt, name)
    return worst


def _run(engine, pattern, probe_items, timeout):
    with mp.get_context("fork").Pool(1) as pool:
        job = pool.apply_async(_time_worker, ((engine, pattern, probe_items),))
        try:
            return job.get(timeout=timeout)
        except mp.TimeoutError:
            pool.terminate()
            return None


def time_rule(engine, pattern, probe_items):
    """Worst single-probe search time. The timeout applies PER PROBE: all probes first run in one
    worker (fast path, budget TIMEOUT_S); if that budget is exceeded, each probe is re-run alone with
    its own TIMEOUT_S, so a slow-but-linear rule is not reported as a single-probe timeout."""
    result = _run(engine, pattern, probe_items, TIMEOUT_S)
    if result is not None:
        return result
    worst = (0.0, None)
    for item in probe_items:
        alone = _run(engine, pattern, [item], TIMEOUT_S)
        dt, name = alone if alone is not None else (float("inf"), f"TIMEOUT:{item[0]}")
        if dt > worst[0]:
            worst = (dt, name)
    return worst


def load_corpus(corpus_dir, limit_files=2500, max_bytes=200_000):
    docs = []
    for path in sorted(Path(corpus_dir).rglob("*")):
        if path.suffix in {".py", ".md", ".txt", ".toml", ".json", ".cfg", ".ini", ".yaml", ".yml"} and path.is_file():
            data = path.read_bytes()[:max_bytes]
            docs.append(data.decode("utf-8", "replace"))
            if len(docs) >= limit_files:
                break
    return docs


def main(toml_path, corpus_dir, out_path):
    raw = Path(toml_path).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == EXPECTED_SHA256, "rule file is not the pinned v8.30.1"
    rules = [r for r in tomllib.loads(raw.decode())["rules"] if "regex" in r]
    corpus = load_corpus(corpus_dir)
    results = []
    for n, rule in enumerate(rules, 1):
        pattern = rule["regex"]
        comp = compile_all(pattern)
        entry = {"id": rule["id"], "compile": {k: v[1] for k, v in comp.items()},
                 "compiled": {k: v[0] is not None for k, v in comp.items()}}
        pos = positives(pattern) if comp["re"][0] is not None else []
        entry["positives"] = len(pos)
        docs = corpus + [f'config = {{"key": "{p}"}}\n' for p in pos]
        entry["span_diff_docs"], entry["span_diff_example"] = {}, {}
        if comp["re2"][0] is not None:
            for eng in ("re", "re_ascii"):
                if comp[eng][0] is None:
                    continue
                diffs = [d for d in docs if spans(comp[eng][0], d) != spans(comp["re2"][0], d)]
                entry["span_diff_docs"][eng] = len(diffs)
                if diffs:
                    d = diffs[0]
                    entry["span_diff_example"][eng] = {
                        eng: [d[a:b][:80] for a, b in spans(comp[eng][0], d)][:3],
                        "re2": [d[a:b][:80] for a, b in spans(comp["re2"][0], d)][:3]}
        probe_items = list(probes(rule, pos).items())
        entry["worst"] = {}
        for eng in ENGINES:
            if comp[eng][0] is not None:
                dt, name = time_rule(eng, pattern, probe_items)
                entry["worst"][eng] = {"seconds": dt, "probe": name}
        results.append(entry)
        print(f"[{n}/{len(rules)}] {rule['id']}", file=sys.stderr, flush=True)
    Path(out_path).write_text(json.dumps({"rules": results, "corpus_docs": len(corpus),
                                          "corpus_chars": sum(map(len, corpus)),
                                          "probe_len": PROBE_LEN, "timeout_s": TIMEOUT_S}, indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:4])
