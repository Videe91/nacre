"""
TEMPORARY pre-commit secret scanner (D-0010). Replaced by src/nacre/ledger/strip_secrets.py once
INDEX #6 is built; delete this file then.

Scans the STAGED content of added/modified files with the vendored gitleaks v8.30.1 rules under
google-re2 (D-0007, D-0009). Honours, as gitleaks does:
  - keywords prefilter (a rule runs only if one of its keywords occurs, case-insensitively);
  - secret selection: secretGroup if set, else the first non-empty capture group, else the match;
  - per-rule entropy: a finding is dropped if the secret's Shannon entropy is <= the rule's threshold;
  - global allowlist (paths, regexes, stopwords) and per-rule allowlists (paths, regexes with
    regexTarget secret|match|line, stopwords, condition OR|AND).
Path-only rules (no regex) are skipped. Findings print redacted; exit 1 if any.

Usage:  python scripts/scan_staged_secrets.py            (staged files; used by the hook)
        python scripts/scan_staged_secrets.py FILE...    (files on disk; for tests and audits)
"""
import math
import subprocess
import sys
import tomllib
from collections import Counter
from pathlib import Path

import re2

RULES_PATH = Path(__file__).resolve().parent.parent / "src/nacre/ledger/data/gitleaks-v8.30.1.toml"

# Nacre allowlist (D-0010): (path regex, secret regex or None for the whole file, reason).
# Every entry needs a reason. Never add an entry to silence a real credential.
NACRE_ALLOWLIST = [
    (r"^src/nacre/ledger/data/gitleaks-v[0-9.]+\.toml$", None, "the vendored rule set itself"),
    (r"^tests/conftest\.py$", r'^"nacre_test_only"$', "password of throwaway roles in the ephemeral Docker test DB"),
    (r"^docs/assumptions/evidence/A-0010-provider-formats[a-z-]*\.md$", r"[`|]",
     "PEM armor labels quoted in format docs; the private-key rule spans prose between them. "
     "A real PEM body never contains a backtick or a table pipe"),
]
_NACRE_ALLOW = [(re2.compile(p), re2.compile(s) if s else None) for p, s, _ in NACRE_ALLOWLIST]


def load(path=RULES_PATH):
    cfg = tomllib.loads(path.read_text())
    glob = cfg.get("allowlist", {})
    global_allow = {
        "paths": [re2.compile(p) for p in glob.get("paths", [])],
        "regexes": [re2.compile(r) for r in glob.get("regexes", [])],
        "stopwords": [s.lower() for s in glob.get("stopwords", [])],
    }
    rules = []
    for r in cfg["rules"]:
        if "regex" not in r:
            continue
        allows = [{
            "condition": a.get("condition", "OR").upper(),
            "target": a.get("regexTarget", "secret"),
            "paths": [re2.compile(p) for p in a.get("paths", [])],
            "regexes": [re2.compile(x) for x in a.get("regexes", [])],
            "stopwords": [s.lower() for s in a.get("stopwords", [])],
        } for a in r.get("allowlists", [])]
        rules.append({"id": r["id"], "regex": re2.compile(r["regex"]),
                      "keywords": [k.lower() for k in r.get("keywords", [])],
                      "entropy": r.get("entropy"), "group": r.get("secretGroup", 0), "allows": allows})
    return global_allow, rules


def entropy(s):
    counts, n = Counter(s), len(s)
    return -sum(c / n * math.log2(c / n) for c in counts.values()) if n else 0.0


def _rule_allows(allow, path, secret, match, line):
    target = {"secret": secret, "match": match, "line": line}.get(allow["target"], secret)
    checks = []
    if allow["paths"]:
        checks.append(any(p.search(path) for p in allow["paths"]))
    if allow["regexes"]:
        checks.append(any(x.search(target) for x in allow["regexes"]))
    if allow["stopwords"]:
        checks.append(any(w in secret.lower() for w in allow["stopwords"]))
    if not checks:
        return False
    return all(checks) if allow["condition"] == "AND" else any(checks)


def scan(path, text, global_allow, rules):
    if any(p.search(path) for p in global_allow["paths"]):
        return []
    if any(p.search(path) and s is None for p, s in _NACRE_ALLOW):
        return []
    lowered, findings = text.lower(), []
    for rule in rules:
        if rule["keywords"] and not any(k in lowered for k in rule["keywords"]):
            continue
        for m in rule["regex"].finditer(text):
            groups = m.groups()
            if rule["group"]:
                secret = groups[rule["group"] - 1] if len(groups) >= rule["group"] else None
            else:
                secret = next((g for g in groups if g), None) or m.group(0)
            if not secret:
                continue
            if rule["entropy"] and entropy(secret) <= rule["entropy"]:
                continue
            if any(x.search(secret) for x in global_allow["regexes"]):
                continue
            if any(w in secret.lower() for w in global_allow["stopwords"]):
                continue
            start = text.rfind("\n", 0, m.start()) + 1
            end = text.find("\n", m.end())
            line = text[start:end if end != -1 else len(text)]
            if any(_rule_allows(a, path, secret, m.group(0), line) for a in rule["allows"]):
                continue
            if any(p.search(path) and s is not None and s.search(secret) for p, s in _NACRE_ALLOW):
                continue
            findings.append((path, text.count("\n", 0, m.start()) + 1, rule["id"], secret))
    return findings


def staged_files():
    out = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=AM", "-z"],
                         capture_output=True, check=True).stdout
    for name in filter(None, out.decode().split("\0")):
        yield name, subprocess.run(["git", "show", f":{name}"], capture_output=True, check=True).stdout


def main(argv):
    global_allow, rules = load()
    files = ((a, Path(a).read_bytes()) for a in argv) if argv else staged_files()
    findings = []
    for name, data in files:
        if b"\0" in data[:8192]:
            continue  # binary
        findings += scan(name, data.decode("utf-8", "replace"), global_allow, rules)
    for path, line, rule_id, secret in findings:
        print(f"SECRET? {path}:{line} [{rule_id}] {secret[:4]}…({len(secret)} chars)", file=sys.stderr)
    if findings:
        print(f"\n{len(findings)} possible secret(s) staged. Remove them, or if a finding is a false "
              "positive, allowlist it (D-0010). Do not weaken a rule.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
