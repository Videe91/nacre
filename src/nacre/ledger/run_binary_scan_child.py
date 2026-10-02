"""
Functionality: The isolated attachment-scan process, end to end: lock itself down, read the attachment from stdin,
  extract and detect, and write the verdict as one JSON object on stdout.
Owns: the child's start-up order (isolation BEFORE any extraction library is imported), running every extracted
  piece through strip_secrets in detection-only mode, turning a finding's offset into a location, collecting the
  versions of the extractors that ran, and the JSON verdict format.
Public entry: detect_binary_secrets(), main()
Decisions: D-0027, D-0006, D-0008, D-0007, D-0011
Assumptions: A-0021, A-0041, A-0010
Notes: Run by ledger/scan_binary_attachment.py as `python -I -B -X utf8 <this file> <limits json>`, with an empty
  environment. -I ignores PYTHON* variables, the user site and the current directory, so sys.path gets the source
  root explicitly (computed from this file, never from the environment).
  - Verdict JSON (one line, the matched value is never in it):
      {"verdict": "clean", "extractors": {name: version}}          # D-0008 amendment 7
      {"verdict": "secret", "rule_id": str, "location": str}
      {"verdict": "unscannable", "reason": str}
  - Any finding -> secret (the first one found); the matched value is never kept, only the rule id and a location
    (member path, page, image region, line). Extraction refusal -> unscannable.
  - strip_secrets is the detector, unchanged. Its redacted text is discarded: nothing is rewritten into a binary.
    It is called with path=None, so gitleaks' path allowlists (vendor/, lock files ...) can never be steered by a
    member name inside an archive (D1).
  - detect_binary_secrets() is the same work without isolation, for in-process tests of the extraction guards
    with scaled-down limits. Production code never calls it outside the child.
"""
import json
import sys
from pathlib import Path


def detect_binary_secrets(data: bytes) -> dict:
    """The verdict for `data` as a JSON-ready dict (see Notes)."""
    from nacre.ledger.extract_binary_text import EXTRACTOR_VERSIONS, ExtractionRefused, extract_binary_text
    from nacre.ledger.strip_secrets import strip_secrets
    if type(data) is not bytes:
        raise TypeError("attachments are bytes")
    used = set()
    try:
        for piece in extract_binary_text(data):
            used.add(piece.extractor)
            findings = strip_secrets(piece.text).findings if piece.text else ()
            if findings:
                return {"verdict": "secret", "rule_id": findings[0].rule_id,
                        "location": _where(piece, findings[0].start)}
    except ExtractionRefused as exc:
        return {"verdict": "unscannable", "reason": str(exc)}
    versions = {}
    for label in sorted(used):
        versions.update(EXTRACTOR_VERSIONS[label])
    return {"verdict": "clean", "extractors": dict(sorted(versions.items()))}


def _where(piece, offset: int) -> str:
    finer = [label for start, label in piece.regions if start <= offset]
    if finer:
        return f"{piece.location}, {finer[-1]}"
    return f"{piece.location}, line {piece.text.count(chr(10), 0, offset) + 1}"


def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))       # .../src
    from nacre.ledger.isolate_scan_process import ScanLimits, isolate_scan_process
    isolate_scan_process(ScanLimits(**json.loads(sys.argv[1])))       # first: before any extraction import
    data = sys.stdin.buffer.read()
    verdict = detect_binary_secrets(data)
    sys.stdout.write(json.dumps(verdict, ensure_ascii=True) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
