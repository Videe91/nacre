"""
Build the H6 negative documents (2026-10-02, built by a separate blind session): secret-free real code.

All 150 files are third-party: .py files from permissively licensed distributions installed in .venv (PACKAGES).
Licence checked at build time against BOTH the distribution metadata (License-Expression, or License) AND its
licence file(s) (listed in License-File, present, and carrying the licence's own wording). Ambiguous,
multi-licence-with-bundled-code, compiled-only and copyleft distributions are not listed (e.g. numpy, tqdm, certifi,
hypothesis, psycopg, cryptography, pydantic_core). Excluded: paths containing test/vendor/third_party/externals/
__pycache__, files whose first 10 lines say "generated", empty files, files in REAL_CREDENTIAL_EXCLUSIONS
(independent detect-secrets pre-scan, see PRESCAN), and anything matching an earlier set by path, original
site-packages path or content sha256 (negatives/, negatives_external/, H2, H3, H4, H5 and the withdrawn H4 first
draw). Identical content inside the pool counts once. Selected by a seeded shuffle (SEED, drawn once from
os.urandom) of the sorted eligible pool, taking files in shuffled order while each distribution stays at or under
PER_PACKAGE_CAP (15% of TOTAL). Each file is truncated at a line boundary to at most 16384 bytes. Output paths are
"<normalised distribution name>/<site-packages path>". Everything is built and validated in a temporary directory
BEFORE the output is replaced. Also writes HOLDOUT6_MANIFEST.json (seed, contexts, classes, hashes).

Run from the repo root:   .venv/bin/python tests/ledger/secret_corpus/build_negatives_holdout6.py
Pre-scan candidate dump:  .venv/bin/python tests/ledger/secret_corpus/build_negatives_holdout6.py --candidates DIR
"""
import email.parser
import hashlib
import json
import random
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SITE = HERE.parents[2] / ".venv" / "lib" / "python3.14" / "site-packages"
OUT = HERE / "negatives_holdout6"
TMP = HERE / ".negatives_holdout6.building"
HOLDOUT_MANIFEST = HERE / "HOLDOUT6_MANIFEST.json"
H4_MANIFEST = HERE / "HOLDOUT4_MANIFEST.json"
EARLIER_DIRS = ("negatives", "negatives_external", "negatives_holdout2", "negatives_holdout3", "negatives_holdout4",
                "negatives_holdout5")
SEED = 471_139_560                  # os.urandom, drawn once 2026-10-02 by the blind H6 session
TOTAL = 150
MAX_BYTES = 16384
PER_PACKAGE_CAP = 22                # floor(15% of TOTAL)
TP_EXCLUDE = ("test", "vendor", "third_party", "externals", "__pycache__")
APPROVED = {"MIT", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0", "PSF-2.0", "MIT OR Apache-2.0"}
LICENCE_WORDING = {                 # text a licence file of that licence must contain
    "MIT": "Permission is hereby granted, free of charge",
    "BSD-2-Clause": "Redistribution and use in source and binary forms",
    "BSD-3-Clause": "Redistribution and use in source and binary forms",
    "Apache-2.0": "Apache License",
    "PSF-2.0": "PYTHON SOFTWARE FOUNDATION LICENSE VERSION 2",
}
# dist name -> (version, licence as stated in metadata License-Expression (or License), top-level path prefixes)
PACKAGES = {
    "annotated-types": ("0.8.0", "MIT", ("annotated_types/",)),
    "anyio": ("4.15.1", "MIT", ("anyio/",)),
    "cbor2": ("6.1.4", "MIT", ("cbor2/",)),
    "click": ("8.5.0", "BSD-3-Clause", ("click/",)),
    "filelock": ("4.0.8", "MIT", ("filelock/",)),
    "fsspec": ("2026.9.0", "BSD-3-Clause", ("fsspec/",)),
    "h11": ("0.16.0", "MIT", ("h11/",)),
    "httpcore": ("1.0.9", "BSD-3-Clause", ("httpcore/",)),
    "httpcore2": ("2.13.1", "BSD-3-Clause", ("httpcore2/",)),
    "httpx2": ("2.13.1", "BSD-3-Clause", ("httpx2/",)),
    "huggingface_hub": ("1.33.0", "Apache-2.0", ("huggingface_hub/",)),
    "idna": ("3.20", "BSD-3-Clause", ("idna/",)),
    "iniconfig": ("2.3.0", "MIT", ("iniconfig/",)),
    "openai": ("3.22.1", "Apache-2.0", ("openai/",)),
    "pluggy": ("1.6.0", "MIT", ("pluggy/",)),
    "pydantic": ("2.13.5", "MIT", ("pydantic/",)),
    "Pygments": ("2.21.0", "BSD-2-Clause", ("pygments/",)),
    "PyYAML": ("6.0.3", "MIT", ("yaml/", "_yaml/")),
    "sniffio": ("1.3.1", "MIT OR Apache-2.0", ("sniffio/",)),
    "truststore": ("0.10.4", "MIT", ("truststore/",)),
    "typing_extensions": ("4.16.0", "PSF-2.0", ("typing_extensions.py",)),
    "typing-inspection": ("0.4.4", "MIT", ("typing_inspection/",)),
}
REAL_CREDENTIAL_EXCLUSIONS: dict[str, str] = {}   # site-packages path -> abstract reason (filled from PRESCAN)
PRESCAN = {
    "tool": "detect-secrets 1.5.0 (Yelp, Apache-2.0), default plugins, `detect-secrets scan --all-files`, scratch "
            "venv outside the repo (certifi 2026.7.22, charset-normalizer 3.5.2, idna 3.20 (scan re-run, identical), "
            "PyYAML 6.0.3, requests 2.34.2, urllib3 2.8.0)",
    "scanned": "every eligible candidate in the pool (after path, generated, empty and disjointness filters), "
               "truncated exactly as it would be written",
    "candidates_scanned": 786, "files_with_hits": 9, "hits": 13, "excluded_as_real_credentials": 0,
    "reviewed_hits": {
        "fsspec implementations/github.py": "basic-auth flag on a docstring URI where org:repo sits in the userinfo "
                                            "position",
        "fsspec implementations/smb.py": "basic-auth flag on a doctest URL with placeholder user and password words",
        "httpx2 _urls.py": "keyword and basic-auth flags on docstring URL anatomy with a placeholder phrase password",
        "huggingface_hub _eval_results.py": "hex flag on a docstring example dataset revision (git commit id)",
        "huggingface_hub _jobs_api.py": "hex flags on docstring/doctest example job and owner object ids",
        "huggingface_hub _revision.py": "hex flag on a doctest output revision (git commit id)",
        "huggingface_hub constants.py": "keyword flag on an HTTP header-name constant (no value)",
        "huggingface_hub hf_api.py": "hex flag on a docstring example commit hash",
        "pydantic networks.py": "basic-auth/keyword flags on docstring URL anatomy and doctest output with placeholder "
                                "userinfo",
    },
    "judgement": "each flagged line read by eye; only a plausible live credential for a real service excludes a "
                 "file; fixtures, hashes, docstring examples and placeholders are kept",
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def norm(dist: str) -> str:
    return dist.lower().replace("-", "_")


def truncate(data: bytes) -> bytes:
    if len(data) <= MAX_BYTES:
        return data
    cut = data.rfind(b"\n", 0, MAX_BYTES)
    return data[:cut + 1] if cut >= 0 else b""


def earlier() -> tuple[set[str], set[str], set[str]]:
    """Paths, original (site-packages-relative) paths and content hashes of every earlier negatives set, plus the
    withdrawn H4 first draw (path and sha256 as pinned in HOLDOUT4_MANIFEST.json)."""
    paths, rels, hashes = set(), set(), set()
    for sub in EARLIER_DIRS:
        for f in json.loads((HERE / sub / "MANIFEST.json").read_text())["files"]:
            paths.add(f["path"])
            rels.update({f["path"], f["path"].split("/", 1)[-1]})
            if f.get("original"):
                rels.add(f["original"].removeprefix("site-packages/"))
            hashes.add(f["sha256"])
    for f in json.loads(H4_MANIFEST.read_text())["negatives"]["withdrawn_first_draw"]["files"]:
        paths.add(f["path"])
        rels.update({f["path"], f["path"].split("/", 1)[-1]})
        hashes.add(f["sha256"])
    return paths, rels, hashes


def meta_of(info: Path):
    return email.parser.HeaderParser().parsestr((info / "METADATA").read_text())


def check_licence(dist: str, version: str, licence: str) -> Path:
    infos = [d for d in SITE.glob("*.dist-info") if (m := meta_of(d))["Name"] == dist and m["Version"] == version]
    assert len(infos) == 1, dist
    meta = meta_of(infos[0])
    stated = meta["License-Expression"] or meta["License"]
    assert licence in APPROVED and stated == licence, (dist, stated)
    files = licence_files(infos[0])
    for part in licence.split(" OR "):
        assert any(LICENCE_WORDING[part] in f.read_text(errors="replace") for f in files), (dist, part)
    return infos[0]


def licence_files(info: Path) -> list[Path]:
    base = info / "licenses" if (info / "licenses").is_dir() else info
    names = meta_of(info).get_all("License-File")
    assert names, info
    files = [base / name for name in names]
    assert all(f.is_file() for f in files), info
    return files


def candidates(paths: set[str], rels: set[str], hashes: set[str]) -> list[tuple[str, str, bytes]]:
    """Every eligible (dist, site-packages path, truncated bytes), sorted, identical content counted once."""
    out, seen = [], set()
    for dist, (version, licence, tops) in sorted(PACKAGES.items()):
        info = check_licence(dist, version, licence)
        owned = {line.split(",")[0] for line in (info / "RECORD").read_text().splitlines()}
        for rel in sorted(owned):
            if not rel.endswith(".py") or not rel.startswith(tops) or any(x in rel.lower() for x in TP_EXCLUDE):
                continue
            data = truncate((SITE / rel).read_bytes())
            head = b"\n".join(data.splitlines()[:10]).lower()
            if not data.strip() or b"generated" in head or rel in REAL_CREDENTIAL_EXCLUSIONS:
                continue
            if f"{norm(dist)}/{rel}" in paths or rel in rels or sha(data) in hashes or sha(data) in seen:
                continue
            seen.add(sha(data))
            out.append((dist, rel, data))
    return out


def select(pool: list[tuple[str, str, bytes]]) -> list[tuple[str, str, bytes]]:
    order = list(pool)
    random.Random(SEED).shuffle(order)
    taken, per = [], {}
    for item in order:
        if len(taken) == TOTAL:
            break
        if per.get(item[0], 0) < PER_PACKAGE_CAP:
            taken.append(item)
            per[item[0]] = per.get(item[0], 0) + 1
    assert len(taken) == TOTAL and PER_PACKAGE_CAP <= 0.15 * TOTAL, (len(taken), TOTAL)
    return taken


def dump_candidates(target: Path) -> None:
    """Write every eligible candidate (as it would be written) for the independent pre-scan."""
    pool = candidates(*earlier())
    for dist, rel, data in pool:
        p = target / norm(dist) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    print(f"dumped {len(pool)} candidates to {target}")


def holdout_manifest(neg: dict, neg_sha: str) -> dict:
    sys.path.insert(0, str(HERE.parent))
    from secret_corpus import holdout_6 as h
    from secret_corpus.corpus import digest
    samples = h.build_holdout6()
    by = {}
    for s in samples:
        by[f"{s.category}/{s.expected}"] = by.get(f"{s.category}/{s.expected}", 0) + 1
    shas = {"corpus_digest": digest(samples),
            "holdout_6_py_sha256": sha((HERE / "holdout_6.py").read_bytes()),
            "negatives_manifest_sha256": neg_sha}
    shas["total"] = sha(json.dumps(shas, sort_keys=True).encode())
    shas["total_method"] = ("sha256 of json.dumps({corpus_digest, holdout_6_py_sha256, negatives_manifest_sha256}, "
                            "sort_keys=True); corpus_digest = secret_corpus.corpus.digest(build_holdout6()), the "
                            "same method that pins H3, H4 and H5")
    n = len(h.HOLDOUT6_CONTEXTS)
    slots = h.HOLDOUT6_CREDENTIAL_SLOTS
    cls = h.HOLDOUT6_CONTEXT_CLASS
    return {
        "holdout": "H6", "sealed": "2026-10-02", "seed": h.HOLDOUT6_SEED,
        "build_call": ("build(seed=HOLDOUT6_SEED, per_generator=50, contexts=HOLDOUT6_CONTEXTS, embed_documents=True, "
                       "cover_all_contexts=True, credential_slots=HOLDOUT6_CREDENTIAL_SLOTS, embed_negatives=False)"),
        "builder_statement": (
            "Sealed 2026-10-02 by a separate session that did not read detector code (strip_secrets.py, "
            "src/nacre/ledger/data/, scripts/scan_staged_secrets.py, scripts/build_credential_slot_regex.py, "
            "scripts/hooks/, tests/ledger/test_strip_secrets.py, measure.py, A-0010 measurement evidence, other "
            "holdout-log sections, docs/decisions/, docs/state/, git history) and ran no detector, hook or "
            "measurement. Seed and negatives seed drawn once from os.urandom. Common/long-tail classes fixed in "
            "holdout_6.py before the build, from public prevalence data (sources in its docstring). Never consult "
            "H6 samples or results while writing or tuning rules."),
        "pre_registration": "A-0010 holdout log, 'PRE-REGISTRATION: gate item 12, revised' (owner, 2026-10-02)",
        "contexts": {"count": n, "credential_slots": sorted(slots),
                     "non_slot": sorted(set(range(n)) - slots),
                     "class": {str(i): cls[i] for i in sorted(cls)},
                     "common": sorted(i for i in cls if cls[i] == "common"),
                     "long_tail": sorted(i for i in cls if cls[i] == "long-tail"),
                     "command_line_slots": sorted(h.HOLDOUT6_COMMAND_LINE_SLOTS),
                     "attribute_pair_slots": sorted(h.HOLDOUT6_ATTRIBUTE_PAIR_SLOTS),
                     "heredoc_slots": sorted(h.HOLDOUT6_HEREDOC_SLOTS),
                     "benign_entropy_non_slots": sorted(h.HOLDOUT6_BENIGN_ENTROPY_NON_SLOTS),
                     "descriptions": "holdout_6.py module docstring"},
        "samples": {"total": len(samples), "by_category_expected": dict(sorted(by.items()))},
        "negatives_seed": SEED,
        "negatives": {"count": len(neg["files"]), "dir": "tests/ledger/secret_corpus/negatives_holdout6",
                      "composition": neg["composition"],
                      "disjoint_from": list(EARLIER_DIRS) + ["H4 withdrawn first draw"],
                      "files": [{"path": f["path"], "sha256": f["sha256"]} for f in neg["files"]]},
        "sha256": shas,
    }


def main() -> None:
    paths, rels, hashes = earlier()
    pool = candidates(paths, rels, hashes)
    chosen = sorted(select(pool), key=lambda t: (t[0], t[1]))
    files, writes = [], {}
    for dist, rel, data in chosen:
        version, licence, _ = PACKAGES[dist]
        out = f"{norm(dist)}/{rel}"
        writes[out] = data
        files.append({"path": out, "sha256": sha(data), "bytes": len(data), "dist": dist, "version": version,
                      "licence": licence, "original": f"site-packages/{rel}"})
    # validate before touching the output directory
    assert len(files) == TOTAL and len({f["path"] for f in files}) == TOTAL
    assert len({f["sha256"] for f in files}) == TOTAL, "duplicate content"
    assert not {f["path"] for f in files} & paths and not {f["sha256"] for f in files} & hashes
    assert not {f["original"].removeprefix("site-packages/") for f in files} & rels
    assert all(f["bytes"] > 0 for f in files)
    used = sorted({t[0] for t in chosen})
    if TMP.exists():
        shutil.rmtree(TMP)
    for rel, data in writes.items():
        (TMP / rel).parent.mkdir(parents=True, exist_ok=True)
        (TMP / rel).write_bytes(data)
    (TMP / "LICENSES").mkdir()
    packages = {}
    for dist in used:
        version, licence, _ = PACKAGES[dist]
        names = []
        for lf in licence_files(check_licence(dist, version, licence)):
            name = f"{norm(dist)}-{lf.name}" + ("" if lf.suffix == ".txt" else ".txt")
            shutil.copyfile(lf, TMP / "LICENSES" / name)
            names.append(f"LICENSES/{name}")
        packages[dist] = {"version": version, "licence": licence, "licence_files": names, "dir": norm(dist),
                          "count": sum(1 for t in chosen if t[0] == dist)}
    manifest = {
        "note": (f"H6 negatives, 2026-10-02, built by a separate blind session (no detector read or run). "
                 f"{len(files)} third-party files from {len(used)} permissively licensed distributions in .venv "
                 f"(seed {SEED}, per-package cap {PER_PACKAGE_CAP}). Disjoint by path, original path and content "
                 f"sha256 from {', '.join(EARLIER_DIRS)} and the H4 withdrawn first draw."),
        "date": "2026-10-02", "built_by": "separate blind session",
        "composition": {"stdlib": 0, "third_party": len(files), "total": len(files)},
        "seeds": {"third_party": SEED}, "site_packages": str(SITE.relative_to(HERE.parents[2])),
        "packages": packages,
        "selection": {"per_package_cap": PER_PACKAGE_CAP, "pool": len(pool),
                      "exclude_substrings": list(TP_EXCLUDE), "generated_rule": "first 10 lines",
                      "licence_rule": "metadata License-Expression (or License) in the approved set AND every License-File "
                                      "present AND each OR-part's own wording in a licence file"},
        "prescan": PRESCAN, "real_credential_exclusions": REAL_CREDENTIAL_EXCLUSIONS, "files": files,
    }
    (TMP / "MANIFEST.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    if OUT.exists():
        shutil.rmtree(OUT)
    TMP.rename(OUT)
    neg_sha = sha((OUT / "MANIFEST.json").read_bytes())
    hm = holdout_manifest(manifest, neg_sha)
    HOLDOUT_MANIFEST.write_text(json.dumps(hm, indent=1, sort_keys=True) + "\n")
    print(f"wrote {len(files)} files from {used} to {OUT}; pool {len(pool)}; negatives manifest {neg_sha}; "
          f"corpus digest {hm['sha256']['corpus_digest']}; total {hm['sha256']['total']}")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--candidates":
        dump_candidates(Path(sys.argv[2]))
    else:
        main()
