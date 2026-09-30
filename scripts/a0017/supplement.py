import hashlib, json, re, subprocess, sys, tomllib, warnings
import re2
sys.path.insert(0, "scripts/a0017")
import eval_regex_engines as ev
warnings.simplefilter("ignore")
rules = [r for r in tomllib.load(open("gitleaks-v8.30.1.toml", "rb"))["rules"] if "regex" in r]
target = [r for r in rules if ev.compile_all(r["regex"])["re"][0] is None]
exp = []
for r in target:
    gen = "(?i)" + r["regex"].replace("(?i)", "")          # generation only; Go stays the judge
    docs = [f'config = {{"key": "{p}"}}\n' for p in ev.positives(gen, n=60)]
    exp.append({"id": r["id"], "regex": r["regex"], "docs": [d for d in docs if d.isprintable() or True]})
json.dump(exp, open("rules22.json", "w")); json.dump([], open("empty.json", "w"))
go = json.loads(subprocess.run(["go", "run", "scripts/a0017/go_spans/main.go", "rules22.json", "empty.json"],
                               capture_output=True, text=True, check=True).stdout)
h = lambda ms: hashlib.sha256("\x00".join(ms).encode()).hexdigest()
empty = h([])
tot_docs = tot_go_hits = tot_diff = 0
for r in exp:
    c = re2.compile(r["regex"]); g = go[r["id"]]
    diff = sum(1 for i, d in enumerate(r["docs"]) if h([m.group(0) for m in c.finditer(d)]) != g[i])
    hits = sum(1 for x in g if x != empty)
    tot_docs += len(r["docs"]); tot_go_hits += hits; tot_diff += diff
    print(f"{r['id']:36s} docs={len(r['docs']):3d} go-matching={hits:3d} re2-vs-go-diff={diff}")
print("TOTAL docs", tot_docs, "go-matching", tot_go_hits, "re2 disagreements", tot_diff)
