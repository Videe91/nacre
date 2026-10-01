"""
Pre-commit secret scan (D-0010). Since INDEX #6 is built, it runs the product's own detector,
src/nacre/ledger/strip_secrets.py (gitleaks under RE2 + Nacre rules + public layer + entropy layer), over the
STAGED content of added/modified files. The temporary gitleaks-only scanner it replaced is gone.
Repo-level additions (not product behaviour):
  - NACRE_ALLOWLIST: reviewed false positives in this repo, each with a reason (D-0010);
  - frozen MNEXA fixtures: exact-value (hashed) reviewed entries, scoped to 017-029 / 030-035 (owner, 2026-10-01);
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
    (r"^(tests/conftest|scripts/bench_append_throughput)\.py$", r"^nacre_dev$",
     "password of the ephemeral local Docker test database (docker-compose.yml, D-0006)"),
    (r"^tests/ledger/secret_corpus/corpus\.py$", r"^0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz$",
     "the Base62 alphabet constant, caught by the entropy layer"),
    (r"^tests/ledger/secret_corpus/generic\.py$", r"^\{_password\(rng\)\}$",
     "f-string template in the corpus generator, not a value"),
    (r"^scripts/scan_staged_secrets\.py$", r"^tests/ledger/secret_corpus/negatives_holdout[23]/(MANIFEST\.json)?$",
     "the H2 negatives path in this script's own manifest table, caught by the entropy layer"),
    (r".*", r"^(x25519\.)?X25519PrivateKey$",
     "the pyca/cryptography class name, matched by generic-api-key after `private_key:` (reviewed FP, "
     "negatives MANIFEST); exact value only"),
]
_NACRE_ALLOW = [(re2.compile(p), re2.compile(s) if s else None) for p, s, _ in NACRE_ALLOWLIST]

# Frozen MNEXA fixtures (owner approval 2026-10-01): exact-value entries, each with a reason, kept as the SHA-256
# of the exact flagged value so the reviewed file does not itself contain the flagged strings. Scoped: an entry is
# honoured only for its exact path, its rule id and its value hash, and only under these two paths.
_FIXTURE_REVIEW_FILE = "tests/regression/mnexa/SECRET_SCAN_REVIEWED.json"
_FIXTURE_REVIEW_SCOPE = re2.compile(
    r"^tests/regression/mnexa/(tasks/tasks_0(1[7-9]|2[0-9])\.json|results/03[0-5]_[A-Za-z0-9_]+\.json)$")


def _fixture_reviews() -> set[tuple[str, str, str]]:
    import json
    try:
        entries = json.loads((ROOT / _FIXTURE_REVIEW_FILE).read_text())["entries"]
    except (OSError, ValueError, KeyError):
        return set()
    out = set()
    for e in entries:
        if not _FIXTURE_REVIEW_SCOPE.search(e["path"]) or not e.get("reason", "").strip():
            raise ValueError(f"reviewed fixture entry outside the approved scope or without a reason: {e['path']}")
        out.add((e["path"], e["rule_id"], e["value_sha256"]))
    return out

# Committed negative corpus (D-0007 amendment 2): skipped ONLY while a file's bytes match the sha256
# recorded in its reviewed manifest. Any edit makes it scanned again.
_NEGATIVE_MANIFESTS = {
    "tests/ledger/secret_corpus/negatives/": "tests/ledger/secret_corpus/negatives/MANIFEST.json",
    "tests/ledger/secret_corpus/negatives_external/": "tests/ledger/secret_corpus/negatives_external/MANIFEST.json",
    # H2 holdout negatives: skipped by manifest sha256 so committing them does not reveal detector results
    # before the boundary fix (D-0011 amendment 5); pre-scanned for real secrets at measurement time.
    "tests/ledger/secret_corpus/negatives_holdout2/": "tests/ledger/secret_corpus/negatives_holdout2/MANIFEST.json",
    "tests/ledger/secret_corpus/negatives_holdout3/": "tests/ledger/secret_corpus/negatives_holdout3/MANIFEST.json",
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
    import hashlib
    out = []
    reviewed = _fixture_reviews() if _FIXTURE_REVIEW_SCOPE.search(path) else set()
    for f in strip_secrets(text, path=path).findings:
        secret = text[f.start:f.end]
        if any(p.search(path) and s is not None and s.search(secret) for p, s in _NACRE_ALLOW):
            continue
        if (path, f.rule_id, hashlib.sha256(secret.encode()).hexdigest()) in reviewed:
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
