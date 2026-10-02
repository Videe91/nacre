"""
Check that the running interpreter's environment matches requirements.lock (D-0006 amendment 3: ALL transitive
runtime dependencies pinned and hash-locked).

Checks:
  1. Every package in requirements.lock is installed at exactly the locked version.
  2. opencv-python (the GUI build) is NOT installed; cv2 must come from opencv-python-headless.
  3. Every lock entry carries at least one sha256 hash (so `--require-hashes` installs are possible).
What it cannot check: which wheel file was installed. Installed metadata keeps no wheel hash, so the hash check
happens at install time (`--require-hashes`), not here.

Install from the lock (hashes enforced; --no-deps so rapidocr's declared opencv-python is never pulled in):
    python -m pip install --require-hashes --no-deps -r requirements.lock
    # or: uv pip install --require-hashes --no-deps -r requirements.lock
Regenerate (keeps existing pins unless --upgrade / --upgrade-package is given; the [tool.uv] override in
pyproject.toml drops opencv-python):
    uv pip compile pyproject.toml --generate-hashes --python-version 3.14 -o requirements.lock
Run:   .venv/bin/python scripts/check_dependency_lock.py     (exit 1 on any mismatch)
"""
import re
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

LOCK = Path(__file__).resolve().parent.parent / "requirements.lock"
FORBIDDEN = ("opencv-python", "opencv-contrib-python")      # GUI builds; the headless build is required
_ENTRY = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s\\;]+)")


def locked(text: str) -> dict[str, tuple[str, int]]:
    """{normalised name: (version, number of hashes)} for every entry of a `uv pip compile` hash lock."""
    entries, current = {}, None
    for line in text.splitlines():
        if m := _ENTRY.match(line):
            current = re.sub(r"[-_.]+", "-", m.group(1)).lower()
            entries[current] = (m.group(2), 0)
        elif current and line.strip().startswith("--hash=sha256:"):
            entries[current] = (entries[current][0], entries[current][1] + 1)
    return entries


def problems(entries: dict[str, tuple[str, int]]) -> list[str]:
    found = []
    if not entries:
        found.append("requirements.lock has no entries")
    for name, (want, hashes) in sorted(entries.items()):
        if hashes == 0:
            found.append(f"{name}: no sha256 hash in the lock")
        try:
            have = version(name)
        except PackageNotFoundError:
            found.append(f"{name}: locked at {want}, not installed")
            continue
        if have != want:
            found.append(f"{name}: locked at {want}, installed {have}")
    for name in FORBIDDEN:
        try:
            found.append(f"{name} {version(name)} is installed; only opencv-python-headless is allowed")
        except PackageNotFoundError:
            pass
    return found


def main() -> int:
    entries = locked(LOCK.read_text())
    found = problems(entries)
    for p in found:
        print("FAIL ", p)
    print(f"{len(entries)} locked package(s), {len(found)} problem(s)")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
