"""
Build the H2 negative documents: secret-free real code from the CPython 3.14.3 standard library
(PSF License). Deterministic: a fixed seed selects 150 .py files from the sorted candidate list.
Each file is truncated at a line boundary to at most 16384 bytes and copied under negatives_holdout2/,
with the PSF licence and a MANIFEST.json (path, sha256, bytes per file).

Run from the repo root: .venv/bin/python tests/ledger/secret_corpus/build_negatives_holdout2.py
"""
import hashlib
import json
import random
import shutil
from pathlib import Path

SOURCE = Path("/opt/homebrew/Cellar/python@3.14/3.14.3_1/Frameworks/Python.framework/"
              "Versions/3.14/lib/python3.14")
OUT = Path(__file__).resolve().parent / "negatives_holdout2"
SEED = 1_402_775_093
COUNT = 150
MAX_BYTES = 16384
EXCLUDE = ("test", "tests", "idlelib", "site-packages", "__pycache__", "lib2to3")


def candidates() -> list[str]:
    paths = []
    for p in SOURCE.rglob("*.py"):
        rel = p.relative_to(SOURCE).as_posix()
        if any(x in rel for x in EXCLUDE):
            continue
        if p.is_file():
            paths.append(rel)
    return sorted(paths)


def truncate(data: bytes) -> bytes:
    if len(data) <= MAX_BYTES:
        return data
    cut = data.rfind(b"\n", 0, MAX_BYTES)
    return data[:cut + 1] if cut >= 0 else b""


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    chosen = sorted(random.Random(SEED).sample(candidates(), COUNT))
    files = []
    for rel in chosen:
        data = truncate((SOURCE / rel).read_bytes())
        dest = OUT / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        files.append({"path": rel, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
    lic = OUT / "LICENSES" / "LICENSE-PSF.txt"
    lic.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SOURCE / "LICENSE.txt", lic)
    manifest = {"source": str(SOURCE), "python_version": "3.14.3", "licence": "PSF-2.0", "files": files}
    (OUT / "MANIFEST.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    print(f"wrote {len(files)} files to {OUT}")


if __name__ == "__main__":
    main()
