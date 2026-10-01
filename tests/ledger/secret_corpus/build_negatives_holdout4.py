"""
Build the H4 negative documents (redraw, 2026-10-01, built by a separate blind session): secret-free real code.

Composition (owner decision 2026-10-01, after the first draw, seed 638022740, was exposed to a detector scan):
  1. Stdlib part: ALL eligible CPython 3.14.3 stdlib .py files (PSF-2.0), no sampling. Eligible = not matching
     EXCLUDE, non-empty after truncation, and disjoint by path AND content sha256 from H2, H3, the withdrawn first
     draw (HOLDOUT4_FIRST_DRAW_NEGATIVES.json), negatives/ and negatives_external/.
  2. Third-party top-up to TOTAL files: .py files from permissively licensed distributions installed in .venv
     (PACKAGES; licence checked against the distribution metadata and its licence files at build time), none of
     them used by negatives/. Excluded: paths containing test/vendor/__pycache__, files whose first 10 lines say
     "generated", empty files, and files in REAL_CREDENTIAL_EXCLUSIONS (independent detect-secrets pre-scan, see
     PRESCAN). Selected by a seeded shuffle (TOPUP_SEED, drawn once from os.urandom) of the sorted eligible pool,
     taking files in shuffled order while each distribution stays under PER_PACKAGE_CAP.
Each file is truncated at a line boundary to at most 16384 bytes. Everything is sampled and validated in a
temporary directory BEFORE the old output is replaced (the previous rmtree-before-sample wiped the output).
Also rewrites the "negatives" block and negatives hashes of HOLDOUT4_MANIFEST.json (corpus digest untouched).

Run from the repo root: .venv/bin/python tests/ledger/secret_corpus/build_negatives_holdout4.py
"""
import email.parser
import hashlib
import json
import random
import shutil
from pathlib import Path

SOURCE = Path("/opt/homebrew/Cellar/python@3.14/3.14.3_1/Frameworks/Python.framework/"
              "Versions/3.14/lib/python3.14")
HERE = Path(__file__).resolve().parent
SITE = HERE.parents[2] / ".venv" / "lib" / "python3.14" / "site-packages"
OUT = HERE / "negatives_holdout4"
TMP = HERE / ".negatives_holdout4.building"
HOLDOUT_MANIFEST = HERE / "HOLDOUT4_MANIFEST.json"
FIRST_DRAW = HERE / "HOLDOUT4_FIRST_DRAW_NEGATIVES.json"
FIRST_DRAW_SEED = 638_022_740
EARLIER_DIRS = ("negatives", "negatives_external", "negatives_holdout2", "negatives_holdout3")
TOPUP_SEED = 4_211_584_703          # os.urandom, drawn once 2026-10-01 by the blind redraw session
TOTAL = 150
MAX_BYTES = 16384
EXCLUDE = ("test", "tests", "idlelib", "site-packages", "__pycache__", "lib2to3")
TP_EXCLUDE = ("test", "vendor", "__pycache__")
APPROVED = {"MIT", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0", "PSF-2.0", "MIT OR Apache-2.0"}
# dist name -> (version, licence as stated in metadata, top-level paths in site-packages)
PACKAGES = {
    "annotated-types": ("0.8.0", "MIT", ("annotated_types",)),
    "anyio": ("4.15.1", "MIT", ("anyio",)),
    "h11": ("0.16.0", "MIT", ("h11",)),
    "httpcore2": ("2.13.1", "BSD-3-Clause", ("httpcore2",)),
    "httpx2": ("2.13.1", "BSD-3-Clause", ("httpx2",)),
    "idna": ("3.20", "BSD-3-Clause", ("idna",)),
    "jiter": ("0.17.0", "MIT", ("jiter",)),
    "openai": ("3.22.1", "Apache-2.0", ("openai",)),
    "pydantic": ("2.13.5", "MIT", ("pydantic",)),
    "pydantic_core": ("2.46.5", "MIT", ("pydantic_core",)),
    "sniffio": ("1.3.1", "MIT OR Apache-2.0", ("sniffio",)),
    "truststore": ("0.10.4", "MIT", ("truststore",)),
    "typing_extensions": ("4.16.0", "PSF-2.0", ("typing_extensions.py",)),
    "typing-inspection": ("0.4.4", "MIT", ("typing_inspection",)),
}
PER_PACKAGE_CAP = 5                 # ~25% of the top-up (asserted below)
REAL_CREDENTIAL_EXCLUSIONS: dict[str, str] = {}   # source path -> abstract reason (none found, see PRESCAN)
PRESCAN = {
    "tool": "detect-secrets 1.5.0 (Yelp, Apache-2.0), default plugins, scratch venv outside the repo",
    "scanned": "every stdlib candidate and every eligible third-party candidate, truncated as written",
    "candidates_scanned": 533, "candidates_scanned_stdlib": 128, "candidates_scanned_third_party": 405,
    "files_with_hits": 4, "excluded_as_real_credentials": 0,
    "reviewed_hits": {
        "stdlib base64.py": "high-entropy flag on a base32 alphabet constant; not a credential",
        "stdlib secrets.py": "high-entropy flag on a doctest example output of token_hex; not a credential",
        "httpx2 _urls.py": "keyword/basic-auth flags on docstring URL examples with placeholder userinfo",
        "pydantic networks.py": "basic-auth/keyword flags on docstring URL examples with placeholder userinfo",
    },
    "judgement": "each flagged line read by eye; only a plausible live credential for a real service excludes a file",
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def truncate(data: bytes) -> bytes:
    if len(data) <= MAX_BYTES:
        return data
    cut = data.rfind(b"\n", 0, MAX_BYTES)
    return data[:cut + 1] if cut >= 0 else b""


def earlier() -> tuple[set[str], set[str], list[dict]]:
    """Paths and content hashes of every earlier negatives set, plus the withdrawn first draw (path, sha256)."""
    paths, hashes = set(), set()
    for sub in EARLIER_DIRS:
        for f in json.loads((HERE / sub / "MANIFEST.json").read_text())["files"]:
            paths.add(f["path"])
            hashes.add(f["sha256"])
    fd = json.loads(FIRST_DRAW.read_text())
    assert fd["seed"] == FIRST_DRAW_SEED
    first = [{"path": p, "sha256": sha(truncate((SOURCE / p).read_bytes()))} for p in fd["paths"]]
    paths |= {f["path"] for f in first}
    hashes |= {f["sha256"] for f in first}
    return paths, hashes, first


def stdlib_candidates(paths: set[str], hashes: set[str]) -> list[tuple[str, bytes]]:
    out = []
    for p in sorted(SOURCE.rglob("*.py")):
        rel = p.relative_to(SOURCE).as_posix()
        if any(x in rel for x in EXCLUDE) or rel in paths or not p.is_file():
            continue
        data = truncate(p.read_bytes())
        if data and sha(data) not in hashes:
            out.append((rel, data))
    return out


def check_licence(dist: str, version: str, licence: str) -> Path:
    infos = [d for d in SITE.glob("*.dist-info")
             if (m := email.parser.HeaderParser().parsestr((d / "METADATA").read_text()))["Name"] == dist
             and m["Version"] == version]
    assert len(infos) == 1, dist
    meta = email.parser.HeaderParser().parsestr((infos[0] / "METADATA").read_text())
    assert licence in APPROVED and (meta["License-Expression"] or meta["License"]) == licence, dist
    assert meta.get_all("License-File"), dist
    return infos[0]


def licence_files(info: Path) -> list[Path]:
    base = info / "licenses" if (info / "licenses").is_dir() else info
    meta = email.parser.HeaderParser().parsestr((info / "METADATA").read_text())
    files = [base / name for name in meta.get_all("License-File")]
    assert files and all(f.is_file() for f in files), info
    return files


def thirdparty_candidates(paths: set[str], hashes: set[str]) -> list[tuple[str, str, bytes]]:
    out = []
    for dist, (version, licence, tops) in PACKAGES.items():
        info = check_licence(dist, version, licence)
        licence_files(info)
        owned = {line.split(",")[0] for line in (info / "RECORD").read_text().splitlines()}
        for rel in sorted(owned):
            if not rel.endswith(".py") or not rel.startswith(tops) or any(x in rel.lower() for x in TP_EXCLUDE):
                continue
            data = truncate((SITE / rel).read_bytes())
            head = b"\n".join(data.splitlines()[:10]).lower()
            if not data or b"generated" in head or rel in REAL_CREDENTIAL_EXCLUSIONS:
                continue
            out_path = f"{dist}-{version}/{rel}"
            if out_path in paths or rel in paths or sha(data) in hashes:
                continue
            out.append((dist, rel, data))
    return out


def select_topup(pool: list[tuple[str, str, bytes]], count: int) -> list[tuple[str, str, bytes]]:
    order = list(pool)
    random.Random(TOPUP_SEED).shuffle(order)
    taken, per = [], {}
    for item in order:
        if len(taken) == count:
            break
        if per.get(item[0], 0) < PER_PACKAGE_CAP:
            taken.append(item)
            per[item[0]] = per.get(item[0], 0) + 1
    assert len(taken) == count and PER_PACKAGE_CAP <= max(2, -(-count // 4)), (len(taken), count)
    return taken


def main() -> None:
    paths, hashes, first = earlier()
    stdlib = stdlib_candidates(paths, hashes)
    hashes_now = hashes | {sha(d) for _, d in stdlib}
    pool = [c for c in thirdparty_candidates(paths, hashes_now)]
    dedup, seen = [], set()
    for c in pool:                          # identical content inside the pool counts once
        if sha(c[2]) not in seen:
            seen.add(sha(c[2]))
            dedup.append(c)
    topup = select_topup(dedup, max(TOTAL - len(stdlib), 0))
    files, writes = [], {}
    for rel, data in stdlib:
        writes[rel] = data
        files.append({"path": rel, "sha256": sha(data), "bytes": len(data), "origin": "stdlib CPython 3.14.3",
                      "licence": "PSF-2.0", "original": rel})
    for dist, rel, data in sorted(topup, key=lambda t: (t[0], t[1])):
        version, licence, _ = PACKAGES[dist]
        out = f"{dist}-{version}/{rel}"
        writes[out] = data
        files.append({"path": out, "sha256": sha(data), "bytes": len(data), "origin": f"{dist} {version}",
                      "licence": licence, "original": f"site-packages/{rel}"})
    files.sort(key=lambda f: f["path"])
    # validate before touching the output directory
    assert len(files) >= TOTAL and len({f["path"] for f in files}) == len(files)
    assert len({f["sha256"] for f in files}) == len(files), "duplicate content"
    assert not {f["path"] for f in files} & paths and not {f["sha256"] for f in files} & hashes
    assert all(f["bytes"] > 0 for f in files)
    used = sorted({t[0] for t in topup})
    if TMP.exists():
        shutil.rmtree(TMP)
    for rel, data in writes.items():
        (TMP / rel).parent.mkdir(parents=True, exist_ok=True)
        (TMP / rel).write_bytes(data)
    (TMP / "LICENSES").mkdir()
    shutil.copyfile(SOURCE / "LICENSE.txt", TMP / "LICENSES" / "LICENSE-PSF.txt")
    packages = {}
    for dist in used:
        version, licence, _ = PACKAGES[dist]
        names = []
        for lf in licence_files(check_licence(dist, version, licence)):
            name = f"{dist}-{version}-{lf.name.replace('/', '_')}" + ("" if lf.suffix == ".txt" else ".txt")
            shutil.copyfile(lf, TMP / "LICENSES" / name)
            names.append(f"LICENSES/{name}")
        packages[dist] = {"version": version, "licence": licence, "licence_files": names,
                          "count": sum(1 for t in topup if t[0] == dist)}
    n_std = len(stdlib)
    manifest = {
        "note": (f"H4 negatives redraw, 2026-10-01, built by a separate blind session (no detector read or run). "
                 f"Composition: {n_std} CPython 3.14.3 stdlib files (all eligible, no seed) + {len(topup)} "
                 f"third-party files (seed {TOPUP_SEED}) = {len(files)}. Replaces the withdrawn first draw "
                 f"(seed {FIRST_DRAW_SEED}). Disjoint by path and content sha256 from {', '.join(EARLIER_DIRS)} "
                 f"and the first draw."),
        "date": "2026-10-01", "built_by": "separate blind session",
        "composition": {"stdlib": n_std, "third_party": len(topup), "total": len(files)},
        "seeds": {"stdlib": None, "third_party": TOPUP_SEED, "withdrawn_first_draw": FIRST_DRAW_SEED},
        "source": str(SOURCE), "python_version": "3.14.3", "stdlib_licence": "PSF-2.0",
        "stdlib_licence_file": "LICENSES/LICENSE-PSF.txt", "packages": packages,
        "selection": {"per_package_cap": PER_PACKAGE_CAP, "third_party_pool": len(dedup),
                      "third_party_exclude_substrings": list(TP_EXCLUDE), "generated_rule": "first 10 lines"},
        "prescan": PRESCAN, "real_credential_exclusions": REAL_CREDENTIAL_EXCLUSIONS, "files": files,
    }
    (TMP / "MANIFEST.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    if OUT.exists():
        shutil.rmtree(OUT)
    TMP.rename(OUT)
    neg_sha = sha((OUT / "MANIFEST.json").read_bytes())
    hm = json.loads(HOLDOUT_MANIFEST.read_text())
    hm.pop("negatives_seed", None)
    hm["negatives_seeds"] = manifest["seeds"]
    hm["negatives"] = {
        "count": len(files), "dir": "tests/ledger/secret_corpus/negatives_holdout4",
        "composition": manifest["composition"], "note": manifest["note"],
        "disjoint_from": list(EARLIER_DIRS) + ["withdrawn first draw"],
        "files": [{"path": f["path"], "sha256": f["sha256"]} for f in files],
        "withdrawn_first_draw": {"seed": FIRST_DRAW_SEED,
                                 "manifest_sha256": json.loads(FIRST_DRAW.read_text())["manifest_sha256"],
                                 "files": first},
    }
    s = hm["sha256"]
    s["negatives_manifest_sha256"] = neg_sha
    parts = {k: s[k] for k in ("corpus_digest", "holdout_4_py_sha256", "negatives_manifest_sha256")}
    s["total"] = sha(json.dumps(parts, sort_keys=True).encode())
    HOLDOUT_MANIFEST.write_text(json.dumps(hm, indent=1, sort_keys=True) + "\n")
    print(f"wrote {len(files)} files ({n_std} stdlib + {len(topup)} third-party) to {OUT}; "
          f"negatives manifest {neg_sha}; total {s['total']}")


if __name__ == "__main__":
    main()
