"""
Pre-commit secret scan (D-0010). Since INDEX #6 is built, it runs the product's own detector,
src/nacre/ledger/strip_secrets.py (gitleaks under RE2 + Nacre rules + public layer + entropy layer), over the
STAGED content of added/modified files. The temporary gitleaks-only scanner it replaced is gone.
Repo-level additions (not product behaviour):
  - NACRE_ALLOWLIST: reviewed false positives in this repo, each with a reason (D-0010);
  - frozen MNEXA fixtures: exact-value (hashed) reviewed entries, scoped to 017-029 / 030-035 (owner, 2026-10-01);
  - committed negative-corpus files are skipped only while their bytes match their reviewed manifest;
  - FAIL CLOSED on the secret corpus (owner, 2026-10-01): every folder under tests/ledger/secret_corpus/ must be
    registered here as a holdout/negatives skip (_NEGATIVE_MANIFESTS), a sealed binary set (_SEALED_BINARY_DIRS,
    each file must match its manifest sha256) or working data (_WORKING_DATA_DIRS, scanned normally). A staged file
    in any other folder refuses the commit, so a new holdout can never be scanned (revealing detector results) by
    accident. Why: on 2026-10-01 the H4 negatives were staged before being registered and one result was revealed.
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
    (r"^scripts/scan_staged_secrets\.py$", r"^tests/ledger/secret_corpus/negatives_holdout[2-6]/(MANIFEST\.json)?$",
     "the H2 negatives path in this script's own manifest table, caught by the entropy layer"),
    (r"^tests/ledger/secret_corpus/(HOLDOUT5_MANIFEST|negatives_holdout5/MANIFEST)\.json$",
     r"^PyYAML-6\.0\.3/yaml/(constructor|tokens)\.py$",
     "file paths of two H5 real-code negatives in H5's own manifests, caught by the entropy layer (capitalised "
     "distribution name + version + path); sealed files, cannot be restructured; exact values only (2026-10-02)"),
    (r"^tests/ledger/secret_corpus/holdout_6\.py$", r"^(value|\{v\})$",
     "H6 context template and its docstring: the literal word 'value' and the f-string placeholder {v} in a "
     "mongodb+srv:// userinfo; sealed file, exact values only (2026-10-02)"),
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
    # H4 (owner approval 2026-10-01, after its first draw was scanned by accident; see the A-0010 holdout log).
    "tests/ledger/secret_corpus/negatives_holdout4/": "tests/ledger/secret_corpus/negatives_holdout4/MANIFEST.json",
    # H5 (owner 2026-10-02: build, seal and measure once; same skip mechanism as H4, registered before staging).
    "tests/ledger/secret_corpus/negatives_holdout5/": "tests/ledger/secret_corpus/negatives_holdout5/MANIFEST.json",
    "tests/ledger/secret_corpus/negatives_holdout6/": "tests/ledger/secret_corpus/negatives_holdout6/MANIFEST.json",
}

# Fail-closed registry of the other folders under the secret corpus (owner, 2026-10-01).
_CORPUS_ROOT = "tests/ledger/secret_corpus/"
_SEALED_BINARY_DIRS = {   # folder -> manifest whose cases list `case_id` and `png_sha256` (I1, D-0027)
    _CORPUS_ROOT + "i1_images/": _CORPUS_ROOT + "I1_MANIFEST.json",
}
_WORKING_DATA_DIRS = {    # scanned like any other file
    _CORPUS_ROOT + "fonts/",
}


def corpus_folder_error(path: str, data: bytes) -> str | None:
    """None if `path` is outside the corpus, a top-level corpus file, or in a registered folder (sealed binaries must
    match their manifest); otherwise the reason the commit is refused."""
    import hashlib
    import json
    rest = path[len(_CORPUS_ROOT):] if path.startswith(_CORPUS_ROOT) else None
    if rest is None or "/" not in rest:
        return None
    folder = _CORPUS_ROOT + rest.split("/", 1)[0] + "/"
    if folder in _NEGATIVE_MANIFESTS or folder in _WORKING_DATA_DIRS:
        return None
    if folder in _SEALED_BINARY_DIRS:
        manifest = _SEALED_BINARY_DIRS[folder]
        try:
            sealed = {c["case_id"] + ".png": c["png_sha256"] for c in json.loads((ROOT / manifest).read_text())["cases"]}
        except (OSError, ValueError, KeyError):
            return f"{path}: cannot read the sealed manifest {manifest}"
        if sealed.get(path[len(folder):]) == hashlib.sha256(data).hexdigest():
            return None
        return f"{path}: not a sealed file of {manifest} (unlisted, or its sha256 differs)"
    return (f"{path}: folder {folder} is not registered in scripts/scan_staged_secrets.py (holdout skip, sealed "
            "binary or working data); register it first, with owner approval for any skip")


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
    findings, refusals = [], []
    for name, data in files:
        if (why := corpus_folder_error(name, data)) is not None:
            refusals.append(why)
            continue
        if b"\0" in data[:8192]:
            continue  # binary
        if _reviewed_negative(name, data):
            continue
        findings += scan(name, data.decode("utf-8", "replace"))
    for path, line, rule_id, secret in findings:
        print(f"SECRET? {path}:{line} [{rule_id}] {secret[:4]}…({len(secret)} chars)", file=sys.stderr)
    for why in refusals:
        print(f"REFUSED {why}", file=sys.stderr)
    if refusals:
        print(f"\n{len(refusals)} file(s) in unregistered or unsealed secret-corpus folders. Nothing was scanned "
              "there, so no detector result was revealed.", file=sys.stderr)
    if findings:
        print(f"\n{len(findings)} possible secret(s) staged. Remove them, or if a finding is a false "
              "positive, allowlist it (D-0010). Do not weaken a rule.", file=sys.stderr)
        return 1
    return 1 if refusals else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
