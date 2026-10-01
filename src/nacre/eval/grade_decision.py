"""
Functionality: Grade a decision text against a frozen task's grader spec, exactly as MNEXA's harness did.
Owns: the three grader contracts used by the frozen task sets: phrase/regex groups (`grade_text`), the semantic
  family contract (`semantic_grade`), and contamination / non-knowledge regex lists (`contains_any_regex`).
Public entry: grade_text(), semantic_grade(), contains_any_regex(), SemanticGrade
Decisions: D-0016
Assumptions: none
Notes: This is the measuring instrument for EXP-0003, not a memory mechanism (D-0016 amendment 2: L1 is kept as
  instrument validation). It must agree with MNEXA's recorded grades on the stored decisions of 003-016 exactly
  (tests/eval/test_grade_decision.py), or "162/180 on these tasks" would not use the same ruler.
  D1: Python `re` with IGNORECASE | DOTALL, not google-re2 (D-0009 governs secret detection on untrusted input).
  The grader patterns are trusted, frozen task data, and the instrument must have MNEXA's exact `re` semantics.
  Contract details kept from MNEXA (seed_growth.py:34, seed_growth_013.py:318, seed_growth_005.py:88):
  phrase checks are case-insensitive substring tests; a bare string in a group means a one-element group;
  semantic_grade's groups are lists (no bare-string promotion).
  L1 coverage (2,070 recorded verdicts over 003-016) exercises only `all_regex` and `forbidden_regex`: the frozen
  graders use nothing else, and no recorded decision had a forbidden hit while the required groups passed. So the
  phrase lists, `none_regex`, bare-string groups and the forbidden-hit branch are guarded by the contract tests
  only. Mutation run 2026-10-01: 9/9 killed with the full test file; 5 of them survive L1 alone.
"""
import re
from dataclasses import dataclass
from typing import Any

_FLAGS = re.IGNORECASE | re.DOTALL


def _as_group(value: Any) -> tuple:
    return (value,) if isinstance(value, str) else tuple(value)


def grade_text(text: str, grader: dict[str, Any]) -> bool:
    """MNEXA's phrase/regex grader: every all_of / all_regex group matches; no none_of / none_regex matches."""
    low = text.lower()
    for alternatives in grader.get("all_of", []):
        if not any(str(phrase).lower() in low for phrase in _as_group(alternatives)):
            return False
    for forbidden in grader.get("none_of", []):
        if str(forbidden).lower() in low:
            return False
    for alternatives in grader.get("all_regex", []):
        if not any(re.search(p, text, _FLAGS) is not None for p in _as_group(alternatives)):
            return False
    for forbidden in grader.get("none_regex", []):
        if re.search(forbidden, text, _FLAGS):
            return False
    return True


@dataclass(frozen=True)
class SemanticGrade:
    passed: bool
    required_ok: bool
    forbidden_hits: tuple[str, ...]

    @property
    def violation(self) -> bool:
        return not self.passed


def semantic_grade(text: str, grader: dict[str, Any]) -> SemanticGrade:
    """MNEXA's semantic-family grader (013-016): every all_regex group matches; no forbidden_regex matches."""
    required_ok = all(any(re.search(p, text, _FLAGS) for p in alternatives) for alternatives in grader.get("all_regex", []))
    hits = tuple(p for p in grader.get("forbidden_regex", []) if re.search(p, text, _FLAGS))
    return SemanticGrade(passed=required_ok and not hits, required_ok=required_ok, forbidden_hits=hits)


def contains_any_regex(text: str, patterns) -> bool:
    """True if any pattern matches (MNEXA contamination and non-knowledge checks)."""
    return any(re.search(p, text, _FLAGS) is not None for p in patterns)
