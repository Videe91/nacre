"""
Check (or, once, seal) the sealed OCR evaluation set I1 (D-0027 §3, A-0042).

Regenerates every I1 image from tests/ledger/secret_corpus/ocr_i1.py and verifies, against
tests/ledger/secret_corpus/I1_MANIFEST.json: the seed, the Pillow version, the vendored font sha256s, every case's
metadata, the sha256 of every PNG and of its raw pixels, and the total sha256. Prints only counts and hashes,
never a rendered value. Exit 0 = the rendered set is exactly the sealed set; 1 = any mismatch.

Pillow is NOT a Nacre dependency. Run with a Python that has the manifest's exact Pillow version, or set
I1_PYTHON to one and this script re-executes itself with it:
    I1_PYTHON=/path/to/i1-venv/bin/python python scripts/check_i1_set.py
    ... scripts/check_i1_set.py --seal      # writes the manifest; refuses if it already exists
"""
import collections
import hashlib
import json
import os
import platform
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "tests" / "ledger" / "secret_corpus"
MANIFEST = CORPUS / "I1_MANIFEST.json"
STATEMENT = ("sealed by a separate session that did not read detector code; never consult I1 samples or "
             "results while writing or tuning OCR or rules")
META = ("font_px", "gated", "dpi_scale", "scene", "theme", "jpeg_quality", "has_secret", "n_secrets", "kinds",
        "wrapped", "size")

try:
    import PIL
    from PIL import features
except ImportError:
    alt = os.environ.get("I1_PYTHON")
    if alt and not os.environ.get("I1_REEXEC"):
        os.environ["I1_REEXEC"] = "1"
        os.execv(alt, [alt, __file__, *sys.argv[1:]])
    sys.exit("Pillow is not importable here; set I1_PYTHON to a Python with the manifest's Pillow version")

sys.path.insert(0, str(CORPUS.parent))
from secret_corpus import ocr_i1  # noqa: E402


def environment() -> dict:
    return {"pillow": PIL.__version__, "freetype2": features.version("freetype2"), "zlib": features.version("zlib"),
            "libjpeg": features.version("jpg"), "python": platform.python_version(),
            "platform": f"{platform.system()}-{platform.machine()}"}


def font_hashes() -> dict:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(ocr_i1.FONT_DIR.iterdir()) if p.is_file()}


def rows():
    for c in ocr_i1.cases():
        yield {"case_id": c.case_id, "png_sha256": hashlib.sha256(c.png).hexdigest(), "pixels_sha256": c.pixels_sha256,
               "font_px": c.font_px, "gated": c.font_px >= ocr_i1.GATED_MIN_PX, "dpi_scale": c.dpi_scale,
               "scene": c.scene, "theme": c.theme, "jpeg_quality": c.jpeg_quality, "has_secret": c.has_secret,
               "n_secrets": len(c.expected_values), "kinds": c.kinds, "wrapped": c.wrapped, "size": list(c.size)}


def total_sha256(rs) -> str:
    return hashlib.sha256("".join(f"{r['case_id']}:{r['png_sha256']}:{r['pixels_sha256']}\n" for r in rs).encode()).hexdigest()


def summary(rs) -> None:
    secrets = [r for r in rs if r["has_secret"]]
    print(f"cases: {len(rs)} (secret-bearing {len(secrets)}, clean {len(rs) - len(secrets)}); "
          f"rendered secrets: {sum(r['n_secrets'] for r in secrets)} "
          f"(gated >= {ocr_i1.GATED_MIN_PX}px: {sum(r['n_secrets'] for r in secrets if r['gated'])}; "
          f"wrapped over lines: {sum(sum(r['wrapped']) for r in secrets)})")
    for key in ("scene", "font_px", "dpi_scale", "theme"):
        by = collections.Counter((r[key], r["has_secret"]) for r in rs)
        print(f"  by {key}: " + ", ".join(f"{v}={by[v, True]}s/{by[v, False]}c" for v in sorted({k for k, _ in by})))
    print(f"  jpeg round trip: {sum(1 for r in rs if r['jpeg_quality'])}; "
          f"secrets per image: {dict(sorted(collections.Counter(r['n_secrets'] for r in secrets).items()))}")


def seal() -> int:
    if MANIFEST.exists():
        sys.exit("I1_MANIFEST.json exists: I1 is sealed; never re-seal")
    t0 = time.monotonic()
    rs = list(rows())
    MANIFEST.write_text(json.dumps({
        "set": "I1", "date": ocr_i1.I1_DATE, "statement": STATEMENT, "seed": ocr_i1.I1_SEED,
        "environment": environment(), "fonts": font_hashes(), "gated_min_font_px": ocr_i1.GATED_MIN_PX,
        "n_secret_cases": ocr_i1.N_SECRET, "n_clean_cases": ocr_i1.N_CLEAN, "total_sha256": total_sha256(rs),
        "cases": rs}, indent=1) + "\n")
    summary(rs)
    print(f"sealed: total sha256 {total_sha256(rs)}; render {time.monotonic() - t0:.1f}s")
    return 0


def check() -> int:
    m, errors = json.loads(MANIFEST.read_text()), []
    env = environment()
    if m["seed"] != ocr_i1.I1_SEED:
        errors.append("seed differs from the manifest")
    if env["pillow"] != m["environment"]["pillow"]:
        errors.append(f"Pillow {env['pillow']} != sealed {m['environment']['pillow']}")
    for k in ("freetype2", "zlib", "libjpeg", "platform"):
        if env[k] != m["environment"][k]:
            print(f"note: {k} {env[k]} != sealed {m['environment'][k]} (images may differ)")
    if font_hashes() != m["fonts"]:
        errors.append("vendored font files differ from the manifest")
    t0 = time.monotonic()
    rs = list(rows())
    elapsed = time.monotonic() - t0
    sealed = {r["case_id"]: r for r in m["cases"]}
    if [r["case_id"] for r in rs] != [r["case_id"] for r in m["cases"]]:
        errors.append("case ids or order differ")
    bad_png = bad_pix = bad_meta = 0
    for r in rs:
        s = sealed.get(r["case_id"], {})
        bad_png += r["png_sha256"] != s.get("png_sha256")
        bad_pix += r["pixels_sha256"] != s.get("pixels_sha256")
        if any(r[k] != s.get(k) for k in META):
            bad_meta += 1
            errors.append(f"{r['case_id']}: metadata differs")
    total = total_sha256(rs)
    summary(rs)
    print(f"png sha256 mismatches: {bad_png}; pixel sha256 mismatches: {bad_pix}; metadata mismatches: {bad_meta}")
    print(f"total sha256: {total} (sealed {m['total_sha256']})")
    print(f"render time: {elapsed:.1f}s for {len(rs)} images; Pillow {env['pillow']}")
    if bad_png or bad_pix:
        errors.append("rendered images differ from the sealed set")
    if total != m["total_sha256"]:
        errors.append("total sha256 differs")
    for e in errors:
        print("FAIL:", e)
    print("I1 OK: rendered set is byte-identical to the sealed set" if not errors else "I1 FAILED")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(seal() if sys.argv[1:] == ["--seal"] else check())
