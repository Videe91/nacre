import json, math, sys
d = json.load(open(sys.argv[1]))
R = d["rules"]
print("rules", len(R), "corpus docs", d["corpus_docs"], "chars", d["corpus_chars"], "probe_len", d["probe_len"])
for eng in ("re", "re_ascii", "re2"):
    fails = [(r["id"], r["compile"][eng]) for r in R if not r["compiled"][eng]]
    warns = [(r["id"], r["compile"][eng]) for r in R if r["compiled"][eng] and r["compile"][eng]]
    print(f"\n{eng}: compile failures {len(fails)}, warnings {len(warns)}")
    for x in fails + warns:
        print("   ", x[0], "|", (x[1] or "")[:160])
print("\npositives generated: total", sum(r["positives"] for r in R), "rules with 0:", [r["id"] for r in R if r["positives"] == 0])
for eng in ("re", "re_ascii"):
    diff = [r for r in R if r["span_diff_docs"].get(eng)]
    print(f"\nspan disagreement {eng} vs re2: {len(diff)} rules")
    for r in diff:
        print("   ", r["id"], r["span_diff_docs"][eng], "docs |", json.dumps(r["span_diff_example"][eng])[:300])
for eng in ("re", "re_ascii", "re2"):
    w = sorted(((r["worst"][eng]["seconds"], r["id"], r["worst"][eng]["probe"]) for r in R if eng in r["worst"]), reverse=True)
    slow = [x for x in w if x[0] > 0.1]
    print(f"\nworst-case {eng}: max {w[0][0]:.4f}s ({w[0][1]}, {w[0][2]}); median {w[len(w)//2][0]:.5f}s; >0.1s: {len(slow)}; timeouts: {sum(1 for x in w if math.isinf(x[0]))}")
    for x in w[:12]:
        print(f"    {x[0]:.4f}s  {x[1]}  {x[2]}")
