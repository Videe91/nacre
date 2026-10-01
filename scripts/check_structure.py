"""
Checks the repo rules mechanically:
  1. Every source file has the required header (Functionality, Public entry, Decisions, Assumptions).
  2. Every Decision / Assumption ID it cites actually exists in docs/.
  3. No file is over the hard line limit.
  4. Every source file is registered in docs/modules/INDEX.md.
  5. No utils.py / helpers.py grab-bag files.
  6. Provider SDKs are imported only by their adapter (D-0021 SI-4).
Run: python scripts/check_structure.py   (exit code 1 on any failure)
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "nacre"
HARD_LIMIT, SOFT_LIMIT = 400, 300
REQUIRED = ["Functionality:", "Public entry:", "Decisions:", "Assumptions:"]
BANNED_NAMES = {"utils.py", "helpers.py", "misc.py", "common.py"}
# D-0021 SI-4: each provider SDK may be imported by exactly one adapter file.
SDK_ADAPTERS = {"openai": "src/nacre/models/openai_responses_provider.py",
                "anthropic": "src/nacre/models/anthropic_messages_provider.py"}
SDK_IMPORT = re.compile(r"^\s*(?:import|from)\s+(openai|anthropic)\b", re.M)


def known_ids():
    decisions = set(re.findall(r"D-\d{4}", " ".join(
        p.read_text() for p in (ROOT / "docs" / "decisions").glob("*.md"))))
    assumptions = set(re.findall(r"A-\d{4}", (ROOT / "docs" / "assumptions" / "ASSUMPTIONS.md").read_text()))
    return decisions, assumptions


def main():
    errors, warnings = [], []
    decisions, assumptions = known_ids()
    index = (ROOT / "docs" / "modules" / "INDEX.md").read_text()

    for path in sorted(SRC.rglob("*.py")):
        if path.name == "__init__.py":
            continue
        rel = path.relative_to(ROOT).as_posix()
        text = path.read_text()
        lines = text.count("\n") + 1

        if path.name in BANNED_NAMES:
            errors.append(f"{rel}: grab-bag file name is banned; give it one functionality")
        header = text.split('"""')[1] if text.lstrip().startswith('"""') else ""
        for field in REQUIRED:
            if field not in header:
                errors.append(f"{rel}: header missing '{field}'")
        for d in re.findall(r"D-\d{4}", header):
            if d not in decisions:
                errors.append(f"{rel}: cites {d}, which has no ADR")
        for a in re.findall(r"A-\d{4}", header):
            if a not in assumptions:
                errors.append(f"{rel}: cites {a}, which is not in ASSUMPTIONS.md")
        if lines > HARD_LIMIT:
            errors.append(f"{rel}: {lines} lines > {HARD_LIMIT}; split into two functionalities")
        elif lines > SOFT_LIMIT:
            warnings.append(f"{rel}: {lines} lines, nearing limit")
        if rel not in index:
            errors.append(f"{rel}: not registered in docs/modules/INDEX.md")
        for sdk in SDK_IMPORT.findall(text):
            if rel != SDK_ADAPTERS[sdk]:
                errors.append(f"{rel}: imports the {sdk} SDK; only {SDK_ADAPTERS[sdk]} may (D-0021 SI-4)")

    for w in warnings:
        print("WARN ", w)
    for e in errors:
        print("FAIL ", e)
    print(f"\n{len(errors)} failure(s), {len(warnings)} warning(s)")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
