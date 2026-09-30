"""
A-0010 measurement (D-0007 amendments 3-5). Test code only.
  catch rate      per covered provider and per generic kind: share of "secret" samples whose secret
                  string no longer occurs after stripping. Target >= 99% EACH.
  public          per public kind: share of "public" samples whose value survives UNCHANGED (neutral:
                  excluded from both rates, reported separately).
  false positives document level: share of negative documents (committed files + synthetic) with at
                  least one redaction. Target <= 2%. Findings per MB are reported alongside.
  coverage        gitleaks rules with at least one covered sample that they caught / total rules;
                  the unmeasured rules are listed by name (in the report, not asserted).
  strict          (D-0011 amendment 8, gates from H3) caught = every character of the secret PART redacted
                  (documented public prefixes / format markers may remain); longest surviving fragment
                  is reported per group. `rate` (whole string gone) and `residue` are kept for the record.
  holdout         (D-0011 amendment 1) official rates come from build_holdout() + the holdout half of
                  the committed negatives; the working set is reported alongside, and a gap between
                  them is flagged as overfitting (overfitting_flags()).
"""
import json
from collections import defaultdict

from secret_corpus.corpus import secret_parts
from pathlib import Path

CORPUS_DIR = Path(__file__).resolve().parent


def _split_of(name: str) -> str:
    """Committed negatives are split in half by a hash of their path: "working" or "holdout"."""
    import hashlib
    return "holdout" if hashlib.sha256(name.encode()).digest()[0] & 1 else "working"


def negative_documents(split: str = "all"):
    """split: "working" / "holdout" (H1 half, now working data too) / "all" over the first negative set,
    or "holdout2": the fresh CPython stdlib negatives sealed for H2."""
    if split in ("holdout2", "holdout3"):
        sub = f"negatives_{split}"
        manifest = json.loads((CORPUS_DIR / sub / "MANIFEST.json").read_text())
        return [(f"{sub}/{f['path']}", (CORPUS_DIR / sub / f["path"]).read_text(errors="replace"))
                for f in manifest["files"]]
    docs = []
    for sub in ("negatives", "negatives_external"):
        manifest = json.loads((CORPUS_DIR / sub / "MANIFEST.json").read_text())
        for f in manifest["files"]:
            name = f"{sub}/{f['path']}"
            if split == "all" or _split_of(name) == split:
                docs.append((name, (CORPUS_DIR / sub / f["path"]).read_text(errors="replace")))
    return docs


def measure(samples, strip, split: str = "all"):
    """strip(text) -> StripResult. `split` selects the committed negatives ("working"/"holdout"/"all").
    Returns the report dict."""
    groups = defaultdict(lambda: {"n": 0, "ok": 0, "misses": [], "residue": 0, "strict": 0, "longest": 0})
    rules_hit = defaultdict(int)
    for s in samples:
        if s.expected == "negative":
            continue
        result = strip(s.text)
        key = (f"{s.category}:{s.provider}" if s.category == "provider"
               else f"credential-slot:{s.kind}" if s.category == "credential-slot"
               else f"generic:{s.provider}/{s.kind}")
        key = f"public:{s.provider}/{s.kind}" if s.expected == "public" else key
        g = groups[key]
        g["n"] += 1
        ok = (s.secret in result.text) if s.expected == "public" else (s.secret not in result.text)
        g["ok"] += ok
        if ok and s.expected == "secret" and len(s.secret) >= 16:
            g["residue"] += any(s.secret[i:i + 16] in result.text for i in range(len(s.secret) - 15))
        if s.expected == "secret":
            strict, longest = _per_character(s, result)
            g["strict"] += strict
            g["longest"] = max(g["longest"], longest)
        if not ok and len(g["misses"]) < 3:
            g["misses"].append(s.kind + " | context: " + s.text[:40].replace(s.secret, "<SECRET>"))
        for rid in result.redactions:
            rules_hit[rid] += 1
    negatives = [(f"synthetic/{s.kind}", s.text) for s in samples if s.expected == "negative"] + negative_documents(split)
    fp_docs, findings, total_bytes = [], 0, 0
    for name, text in negatives:
        r = strip(text)
        total_bytes += len(text.encode())
        findings += len(r.findings)
        if r.findings:
            fp_docs.append((name, sorted(set(r.redactions))))
    return {
        "groups": {k: {"n": v["n"], "rate": v["ok"] / v["n"], "misses": v["misses"],
                       "residue_rate": v["residue"] / v["ok"] if v["ok"] else 0.0,
                       "strict_rate": v["strict"] / v["n"], "longest_fragment": v["longest"]}
                   for k, v in sorted(groups.items())},
        "fp_rate": len(fp_docs) / len(negatives), "fp_docs": fp_docs, "negatives": len(negatives),
        "fp_findings_per_mb": findings / (total_bytes / 1e6), "rules_hit": dict(rules_hit),
    }


def overfitting_flags(working, holdout, threshold=0.01):
    """Groups whose working-set rate exceeds the holdout rate by more than `threshold` (1 point)."""
    return {g: (working["groups"][g]["rate"], holdout["groups"][g]["rate"])
            for g in holdout["groups"] if g in working["groups"]
            and working["groups"][g]["rate"] - holdout["groups"][g]["rate"] > threshold}


def _per_character(sample, result):
    """D-0011 amendment 8: caught iff every character of every secret part is inside a redaction span
    (StripResult.findings are offsets in the original text). Returns (caught, longest surviving run)."""
    base = sample.text.find(sample.secret)
    covered = set()
    for f in result.findings:
        covered.update(range(f.start, f.end))
    longest = run = 0
    for start, end in secret_parts(sample):
        for i in range(base + start, base + end):
            run = 0 if i in covered else run + 1
            longest = max(longest, run)
        run = 0
    return longest == 0, longest
