"""
Build the committed negative corpus (D-0007 amendments 2-3). Run from the repo root:
    .venv/bin/python tests/ledger/secret_corpus/build_negatives.py
Deterministic for a given .venv. Selects files from PERMISSIVELY licensed installed packages only
(MIT, MIT-0, BSD, Apache-2.0, PSF, per each package's own metadata), copies them with their licence
files, truncates each at a line boundary to MAX_BYTES, and pre-scans every file with the vendored
rules (via scripts/scan_staged_secrets.py). It writes negatives/MANIFEST.json with provenance and each
pre-scan finding (rule id, line, a 4-char preview only).
Review rule (owner): findings that are REAL secrets are excluded, by listing the file in
EXCLUDED_AFTER_REVIEW with the reason. Findings that are false positives are KEPT: they are what the
<= 2% false-positive rate measures.
"""
import hashlib
import importlib.metadata as md
import importlib.util
import json
import random
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent / "negatives"
PERMISSIVE = {"MIT", "MIT-0", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0", "PSF-2.0",
              "Apache-2.0 OR BSD-2-Clause", "Apache-2.0 OR BSD-3-Clause"}
CLASSIFIER_MAP = {"MIT License": "MIT", "BSD License": "BSD-3-Clause", "Apache Software License": "Apache-2.0"}
SUFFIXES = (".py", ".md", ".txt", ".toml", ".json", ".cfg", ".ini", ".yaml", ".yml", ".rst", ".pyi")
EXCLUDE_PARTS = ("pip/_vendor/",)          # vendored third-party code with its own (mixed) licences
FILES_PER_PACKAGE, MAX_BYTES, SEED = 60, 16_384, 20260930
EXCLUDED_AFTER_REVIEW: dict[str, str] = {}  # "package/relative/path": "reason (real secret …)"
REVIEWED_FALSE_POSITIVES = {                 # kept on purpose: these are what the FP rate measures
    "cryptography/cryptography/hazmat/bindings/_rust/openssl/hpke.pyi":
        "generic-api-key on type annotations `private_key: x25519.X25519PrivateKey` (lines 77, 101); not a secret",
}


def licence_of(dist) -> str | None:
    m = dist.metadata
    expr = (m.get("License-Expression") or "").strip()
    if expr:
        return expr if expr in PERMISSIVE else None
    for c in m.get_all("Classifier") or []:
        if c.startswith("License ::") and c.split(" :: ")[-1] in CLASSIFIER_MAP:
            return CLASSIFIER_MAP[c.split(" :: ")[-1]]
    return None


def _scanner():
    spec = importlib.util.spec_from_file_location("scan", ROOT / "scripts/scan_staged_secrets.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def truncate(data: bytes) -> bytes:
    if len(data) <= MAX_BYTES:
        return data
    cut = data.rfind(b"\n", 0, MAX_BYTES)
    return data[: cut + 1 if cut > 0 else MAX_BYTES]


def main():
    scan = _scanner()
    allow, rules = scan.load()
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    rng, manifest = random.Random(SEED), {"packages": {}, "files": []}
    for dist in sorted(md.distributions(), key=lambda d: d.metadata["Name"].lower()):
        name, lic = dist.metadata["Name"], licence_of(dist)
        if lic is None:
            continue
        candidates = sorted(str(f) for f in dist.files or []
                            if str(f).endswith(SUFFIXES) and ".dist-info/" not in str(f)
                            and not any(p in str(f) for p in EXCLUDE_PARTS))
        chosen = sorted(rng.sample(candidates, min(FILES_PER_PACKAGE, len(candidates))))
        licence_files = [str(f) for f in dist.files or [] if ".dist-info/" in str(f)
                         and Path(str(f)).name.upper().startswith(("LICENSE", "LICENCE", "COPYING", "NOTICE"))]
        for lf in licence_files:
            dest = OUT / name / "LICENSES" / Path(lf).name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(Path(dist.locate_file(lf)).read_bytes())
        manifest["packages"][name] = {"version": dist.version, "licence": lic, "licence_files": sorted(
            f"{name}/LICENSES/{Path(lf).name}" for lf in licence_files)}
        for rel in chosen:
            key = f"{name}/{rel}"
            if key in EXCLUDED_AFTER_REVIEW:
                continue
            data = truncate(Path(dist.locate_file(rel)).read_bytes())
            if b"\0" in data[:8192]:
                continue
            dest = OUT / name / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            findings = scan.scan(f"negatives/{key}", data.decode("utf-8", "replace"), allow, rules)
            manifest["files"].append({
                "path": key, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
                "prescan_findings": [{"rule": r, "line": ln, "preview": s[:4] + "…"} for _, ln, r, s in findings],
            })
    manifest["excluded_after_review"] = EXCLUDED_AFTER_REVIEW
    manifest["reviewed_false_positives"] = REVIEWED_FALSE_POSITIVES
    unreviewed = [f["path"] for f in manifest["files"] if f["prescan_findings"] and f["path"] not in REVIEWED_FALSE_POSITIVES]
    if unreviewed:
        print("UNREVIEWED findings (review, then list in EXCLUDED_AFTER_REVIEW or REVIEWED_FALSE_POSITIVES):", unreviewed)
    (OUT / "MANIFEST.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    flagged = [f for f in manifest["files"] if f["prescan_findings"]]
    print(f"{len(manifest['files'])} files from {len(manifest['packages'])} packages; "
          f"{sum(f['bytes'] for f in manifest['files'])} bytes; {len(flagged)} with pre-scan findings to review")
    for f in flagged:
        print("  REVIEW", f["path"], f["prescan_findings"])


if __name__ == "__main__":
    main()
