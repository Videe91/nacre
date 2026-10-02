"""Tests for scripts/check_dependency_lock.py (D-0006 amendment 3): the installed environment matches the hash lock."""
import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("check_lock", ROOT / "scripts/check_dependency_lock.py")
check_lock = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_lock)


def test_the_installed_environment_matches_the_lock():
    run = subprocess.run([sys.executable, str(ROOT / "scripts/check_dependency_lock.py")], capture_output=True, text=True)
    assert run.returncode == 0, run.stdout


def test_every_runtime_dependency_is_locked_with_hashes_and_opencv_is_headless():
    entries = check_lock.locked(check_lock.LOCK.read_text())
    for name in ("rapidocr", "pillow", "pypdf", "pypdfium2", "onnxruntime", "opencv-python-headless", "shapely",
                 "pyclipper", "omegaconf", "antlr4-python3-runtime", "requests", "pyyaml", "tqdm", "colorlog", "six",
                 "psycopg", "cryptography", "google-re2", "numpy", "tokenizers"):
        assert name in entries, name
    assert all(hashes > 0 for _, hashes in entries.values())
    assert "opencv-python" not in entries


def test_a_mismatch_or_a_forbidden_package_is_reported():
    assert check_lock.problems({"pypdf": ("0.0.1", 1)}) == ["pypdf: locked at 0.0.1, installed 6.19.0"]
    assert check_lock.problems({"no-such-package-xyz": ("1.0", 1)}) == ["no-such-package-xyz: locked at 1.0, not installed"]
    assert check_lock.problems({"pypdf": ("6.19.0", 0)}) == ["pypdf: no sha256 hash in the lock"]
    assert check_lock.problems({}) == ["requirements.lock has no entries"]
    assert check_lock.locked("foo==1.2 \\\n    --hash=sha256:ab\n    --hash=sha256:cd\n    # via x\nBar_Baz==3\n") == {
        "foo": ("1.2", 2), "bar-baz": ("3", 0)}
