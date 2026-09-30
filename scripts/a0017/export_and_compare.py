"""Export rules/docs for Go, then compare Go (truth) vs re2 and re(ASCII) match lists."""
import hashlib, json, re, subprocess, sys, tomllib, warnings
import re2
sys.path.insert(0, "scripts/a0017")
import eval_regex_engines as ev
warnings.simplefilter("ignore")
rules = [r for r in tomllib.load(open("gitleaks-v8.30.1.toml", "rb"))["rules"] if "regex" in r]
def valid(s):
    try: s.encode("utf-8"); return True
    except UnicodeEncodeError: return False
corpus = [d for d in ev.load_corpus("/Users/vineetpandey/Desktop/nacre/.venv/lib/python3.14/site-packages") if valid(d)]
exp = []
for r in rules:
    pos = ev.positives(r["regex"]) if ev.compile_all(r["regex"])["re"][0] is not None else []
    exp.append({"id": r["id"], "regex": r["regex"], "docs": [d for d in (f'config = {{"key": "{p}"}}\n' for p in pos) if valid(d)]})
json.dump(exp, open("rules.json", "w")); json.dump(corpus, open("corpus.json", "w"))
go = json.loads(subprocess.run(["go", "run", "scripts/a0017/go_spans/main.go", "rules.json", "corpus.json"],
                               capture_output=True, text=True, check=True).stdout)
h = lambda ms: hashlib.sha256("\x00".join(ms).encode()).hexdigest()
res = {"docs_corpus": len(corpus), "rules": {}}
for r in exp:
    g = go[r["id"]]; docs = corpus + r["docs"]
    entry = {"go_compile_error": g[0] if g and g[0].startswith("COMPILE_ERROR") else None}
    for name, flags in (("re2", None), ("re_ascii", re.ASCII)):
        try:
            c = re2.compile(r["regex"]) if flags is None else re.compile(r["regex"], flags)
        except Exception as e:
            entry[name] = {"compile_error": str(e)}; continue
        diffs = [i for i, d in enumerate(docs) if h([m.group(0) for m in c.finditer(d)]) != g[i]]
        entry[name] = {"diff_docs": len(diffs), "example": None}
        if diffs:
            d = docs[diffs[0]]
            entry[name]["example"] = {"engine": [m.group(0)[:80] for m in c.finditer(d)][:2], "go": "see go run"}
    res["rules"][r["id"]] = entry
json.dump(res, open("go_compare.json", "w"), indent=1)
R = res["rules"]
print("docs:", len(corpus), "+ per-rule positives; rules:", len(R), "go compile errors:", sum(1 for e in R.values() if e["go_compile_error"]))
for name in ("re2", "re_ascii"):
    ok = [k for k, e in R.items() if "diff_docs" in e[name]]
    bad = [(k, R[k][name]["diff_docs"]) for k in ok if R[k][name]["diff_docs"]]
    print(f"{name}: compiled {len(ok)}, rules disagreeing with Go: {len(bad)}", bad[:15])
