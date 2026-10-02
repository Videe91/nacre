"""
Functionality: Decide whether one binary attachment may be stored: extract its text, run the secret detector, and
  return the verdict, end to end.
Owns: the verdict types (clean with extractor versions / secret with rule and location / unscannable with reason),
  running every extracted piece through strip_secrets in detection-only mode, turning a finding's offset into a
  location, and the rejection message.
Public entry: scan_binary_attachment(), rejection_message(), Clean, SecretDetected, Unscannable, Verdict
Decisions: D-0027, D-0008, D-0007, D-0011
Assumptions: A-0021, A-0041, A-0010
Notes: D-0027 §2.
  - Any finding -> SecretDetected (the first one found); the matched value is never kept, only the rule id and a
    location (member path, page, image region, line). Extraction refusal -> Unscannable. No opt-out exists
    (owner, D-0027 acceptance): the caller rejects both.
  - strip_secrets is the detector, unchanged. Its redacted text is discarded: nothing is rewritten into a binary.
    It is called with path=None, so gitleaks' path allowlists (vendor/, lock files ...) can never be steered by
    a member name inside an archive (D1).
  - The declared media type is accepted but never consulted: content decides (D-0008 amendment 6).
"""
from dataclasses import dataclass

from nacre.ledger.extract_binary_text import ExtractionRefused, TextPiece, extract_binary_text
from nacre.ledger.strip_secrets import strip_secrets


@dataclass(frozen=True, slots=True)
class Clean:
    extractors: tuple[str, ...]          # every extractor that ran, with its version


@dataclass(frozen=True, slots=True)
class SecretDetected:
    rule_id: str
    location: str


@dataclass(frozen=True, slots=True)
class Unscannable:
    reason: str


type Verdict = Clean | SecretDetected | Unscannable


def scan_binary_attachment(data: bytes, declared_media_type: str | None = None) -> Verdict:
    """The verdict for `data`. `declared_media_type` is ignored for the decision: content decides."""
    if type(data) is not bytes:
        raise TypeError("attachments are bytes")
    used = set()
    try:
        for piece in extract_binary_text(data):
            used.add(piece.extractor)
            findings = strip_secrets(piece.text).findings if piece.text else ()
            if findings:
                return SecretDetected(findings[0].rule_id, _where(piece, findings[0].start))
    except ExtractionRefused as exc:
        return Unscannable(str(exc))
    return Clean(tuple(sorted(used)))


def rejection_message(verdict: Verdict) -> str:
    """The D-0027 error text for a rejected verdict. It never contains a matched value."""
    match verdict:
        case SecretDetected(rule_id, location):
            return f"attachment_rejected: secret_detected (rule {rule_id}, at {location})"
        case Unscannable(reason):
            return f"attachment_rejected: unscannable({reason})"
    raise ValueError("a clean verdict is not a rejection")


def _where(piece: TextPiece, offset: int) -> str:
    finer = [label for start, label in piece.regions if start <= offset]
    if finer:
        return f"{piece.location}, {finer[-1]}"
    return f"{piece.location}, line {piece.text.count(chr(10), 0, offset) + 1}"
