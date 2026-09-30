import re, re2, sys, time, tomllib, multiprocessing as mp
sys.path.insert(0, "scripts/a0017")
import eval_regex_engines as ev
R = {r["id"]: r for r in tomllib.load(open("gitleaks-v8.30.1.toml","rb"))["rules"] if "regex" in r}
def t(args):
    pat, text = args; c = re.compile(pat, re.ASCII); t0 = time.perf_counter(); c.search(text); return time.perf_counter() - t0
if __name__ == "__main__":
    ctx = mp.get_context("fork")
    for rid in ["sumologic-access-id", "okta-access-token", "curl-auth-user", "cohere-api-token", "cisco-meraki-api-key", "privateai-api-token"]:
        r = R[rid]; pos = ev.positives(r["regex"])
        worst = None
        for name, text in ev.probes(r, pos).items():
            row = []
            for n in (250, 500, 1000, 2000):
                with ctx.Pool(1) as p:
                    j = p.apply_async(t, ((r["regex"], text[:n]),))
                    try: row.append(j.get(timeout=20))
                    except mp.TimeoutError: p.terminate(); row.append(float("inf"))
            if worst is None or row[-1] > worst[1][-1]: worst = (name, row)
        c2 = re2.compile(r["regex"]); t0 = time.perf_counter(); c2.search(dict(ev.probes(r, pos))[worst[0]]); r2 = time.perf_counter() - t0
        print(f"{rid:24s} probe={worst[0]!r:28s} re(ASCII) n=250..2000: " + " ".join(f"{x:.3f}s" for x in worst[1]) + f" | re2 @20000: {r2:.5f}s")
