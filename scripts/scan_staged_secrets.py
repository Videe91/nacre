"""
Pre-commit secret scan (D-0010). Since INDEX #6 is built, it runs the product's own detector,
src/nacre/ledger/strip_secrets.py (gitleaks under RE2 + Nacre rules + public layer + entropy layer), over the
STAGED content of added/modified files. The temporary gitleaks-only scanner it replaced is gone.
Repo-level additions (not product behaviour):
  - NACRE_ALLOWLIST: reviewed false positives in this repo, each with a reason (D-0010);
  - committed negative-corpus files are skipped only while their bytes match their reviewed manifest.
Findings print redacted; exit 1 if any.

Usage:  python scripts/scan_staged_secrets.py            (staged files; used by the hook)
        python scripts/scan_staged_secrets.py FILE...    (files on disk; for tests and audits)
"""
import subprocess
import sys
from pathlib import Path

import re2

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from nacre.ledger.strip_secrets import strip_secrets  # noqa: E402

# Nacre allowlist (D-0010): (path regex, secret regex or None for the whole file, reason).
# Every entry needs a reason. Never add an entry to silence a real credential.
NACRE_ALLOWLIST = [
    (r"^src/nacre/ledger/data/gitleaks-v[0-9.]+\.toml$", None, "the vendored rule set itself"),
    (r"^tests/conftest\.py$", r'^"nacre_test_only"$', "password of throwaway roles in the ephemeral Docker test DB"),
    (r"^docs/assumptions/evidence/A-0010-provider-formats[a-z-]*\.md$", r"[`|]",
     "PEM armor labels quoted in format docs; the private-key rule spans prose between them. "
     "A real PEM body never contains a backtick or a table pipe"),
    (r"^docs/assumptions/evidence/A-0010-provider-formats[a-z-]*\.md$", r"^\[password\]\]?$",
     "documented URI syntax placeholders `user:[password]@`, `[user[:[password]]@]` quoted in the format research"),
    (r"^tests/conftest\.py$", r"^nacre_dev$",
     "password of the ephemeral local Docker test database (docker-compose.yml, D-0006)"),
    (r"^tests/ledger/secret_corpus/corpus\.py$", r"^0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz$",
     "the Base62 alphabet constant, caught by the entropy layer"),
    (r"^tests/ledger/secret_corpus/generic\.py$", r"^\{_password\(rng\)\}$",
     "f-string template in the corpus generator, not a value"),
    (r".*", r"^(x25519\.)?X25519PrivateKey$",
     "the pyca/cryptography class name, matched by generic-api-key after `private_key:` (reviewed FP, "
     "negatives MANIFEST); exact value only"),
]
_NACRE_ALLOW = [(re2.compile(p), re2.compile(s) if s else None) for p, s, _ in NACRE_ALLOWLIST]

# Committed negative corpus (D-0007 amendment 2): skipped ONLY while a file's bytes match the sha256
# recorded in its reviewed manifest. Any edit makes it scanned again.
_NEGATIVE_MANIFESTS = {
    "tests/ledger/secret_corpus/negatives/": "tests/ledger/secret_corpus/negatives/MANIFEST.json",
    "tests/ledger/secret_corpus/negatives_external/": "tests/ledger/secret_corpus/negatives_external/MANIFEST.json",
}


def _reviewed_negative(path: str, data: bytes) -> bool:
    import hashlib
    import json
    for prefix, manifest in _NEGATIVE_MANIFESTS.items():
        if path.startswith(prefix):
            try:
                files = json.loads((ROOT / manifest).read_text())["files"]
            except (OSError, ValueError, KeyError):
                return False
            digest = hashlib.sha256(data).hexdigest()
            return any(prefix + f["path"] == path and f["sha256"] == digest for f in files)
    return False


def scan(path: str, text: str) -> list[tuple[str, int, str, str]]:
    """(path, line, rule_id, secret) for each finding strip_secrets reports, minus reviewed repo allowlist."""
    if any(p.search(path) and s is None for p, s in _NACRE_ALLOW):
        return []
    out = []
    for f in strip_secrets(text, path=path).findings:
        secret = text[f.start:f.end]
        if any(p.search(path) and s is not None and s.search(secret) for p, s in _NACRE_ALLOW):
            continue
        out.append((path, text.count("\n", 0, f.start) + 1, f.rule_id, secret))
    return out


def staged_files():
    out = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=AM", "-z"],
                         capture_output=True, check=True).stdout
    for name in filter(None, out.decode().split("\0")):
        yield name, subprocess.run(["git", "show", f":{name}"], capture_output=True, check=True).stdout


def main(argv):
    files = ((a, Path(a).read_bytes()) for a in argv) if argv else staged_files()
    findings = []
    for name, data in files:
        if b"\0" in data[:8192]:
            continue  # binary
        if _reviewed_negative(name, data):
            continue
        findings += scan(name, data.decode("utf-8", "replace"))
    for path, line, rule_id, secret in findings:
        print(f"SECRET? {path}:{line} [{rule_id}] {secret[:4]}…({len(secret)} chars)", file=sys.stderr)
    if findings:
        print(f"\n{len(findings)} possible secret(s) staged. Remove them, or if a finding is a false "
              "positive, allowlist it (D-0010). Do not weaken a rule.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
