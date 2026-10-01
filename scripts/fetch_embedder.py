"""
Fetch the pinned embedder files (D-0024; owner rules 2026-10-01). Not product code; the runtime never downloads.

- Source 1: Hugging Face at the pinned COMMIT (never a branch).
- Source 2 (backup we control): the GitHub release asset `embedder-minilm-l6-v2-1110a243` on Videe91/nacre.
- Every file is verified against the sha256 pinned in src/nacre/recall/embed_local.py before it is moved into place
  (written to a temp name, then renamed). A mismatch from one source falls through to the next; if no source gives
  the pinned bytes, nothing is installed and the exit code is 1.

    .venv/bin/python scripts/fetch_embedder.py [--dir DIR] [--source hf|release|both]
"""
import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from nacre.recall.embed_local import FILES, REVISION, default_model_dir  # noqa: E402

HF = "https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/" + REVISION + "/{path}"
HF_PATHS = {"model.onnx": "onnx/model.onnx", "tokenizer.json": "tokenizer.json"}
RELEASE = "https://github.com/Videe91/nacre/releases/download/embedder-minilm-l6-v2-1110a243/{path}"


def sources(which: str) -> list[tuple[str, str]]:
    out = []
    if which in ("hf", "both"):
        out.append(("hf", HF))
    if which in ("release", "both"):
        out.append(("release", RELEASE))
    return out


def fetch_one(name: str, want: str, dest: Path, srcs: list[tuple[str, str]]) -> str:
    """Install `name` into `dest` from the first source whose bytes match `want`; returns that source's label."""
    target = dest / name
    if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == want:
        return "present"
    for label, pattern in srcs:
        url = pattern.format(path=HF_PATHS[name] if label == "hf" else name)
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                data = r.read()
        except OSError as e:
            print(f"  {name}: {label} unavailable ({type(e).__name__})", file=sys.stderr)
            continue
        if hashlib.sha256(data).hexdigest() != want:
            print(f"  {name}: {label} bytes do NOT match the pinned sha256; ignored", file=sys.stderr)
            continue
        tmp = target.with_suffix(target.suffix + ".part")
        tmp.write_bytes(data)
        tmp.replace(target)
        return label
    raise SystemExit(f"{name}: no source gave the pinned bytes; nothing installed")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, default=None)
    ap.add_argument("--source", choices=("hf", "release", "both"), default="both")
    a = ap.parse_args(argv)
    dest = a.dir or default_model_dir()
    dest.mkdir(parents=True, exist_ok=True)
    for name, want in FILES.items():
        print(f"{name}: {fetch_one(name, want, dest, sources(a.source))} (sha256 {want[:12]}…)")
    print(f"embedder files verified in {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
