"""
Functionality: Admit proposed propositions deterministically: exact grounding in authoritative sections, structure,
  support-first fallback, closed world.
Owns: every admission rule and rejection reason, span resolution (quote -> unique offsets), de-duplication, and the
  fallback rule.
Public entry: admit_propositions(), Proposal, Admitted, Admission, REJECTION_REASONS
Decisions: D-0020, D-0018, D-0017
Assumptions: A-0026
Notes: No model call: the deterministic gate is the checker (D-0020; MNEXA seed 010 negative result: model-chosen
  boundaries without grounding lost 18 -> 14). Rules, in order (MNEXA lessons 006/007/008/011/014, rebuilt):
    1. support: `section` names a section; that section is AUTHORITATIVE (D-0018); `quote` is non-empty and occurs
       EXACTLY ONCE in that section's text -> span. Otherwise a HARD reject (never a fallback): fabricated or
       non-authoritative support never enters memory (gate items 12, 13).
    2. duplicate span -> rejected (`duplicate_span`).
    3. structure: nucleus non-empty and inside the quote; every qualifier has a known type and lies inside the quote.
       Structure fails -> the valid support becomes a FALLBACK (unresolved), unless an admitted structured
       proposition already covers that span (MNEXA 014 "support-first lossless fallback").
  Closed world: nothing outside the given proposals is admitted, and nothing is invented. Input order is preserved.
"""
from dataclasses import dataclass, field

from nacre.sleep.build_evidence_bundle import EvidenceBundle

QUALIFIER_TYPES = frozenset({"condition", "ordering", "scope", "negation"})
REJECTION_REASONS = frozenset({"bad_section", "non_authoritative", "empty_quote", "quote_not_in_source",
                               "quote_ambiguous_in_source", "duplicate_span", "malformed"})


@dataclass(frozen=True)
class Proposal:
    section: object
    quote: object
    nucleus: object = None
    qualifiers: object = ()


@dataclass(frozen=True)
class Admitted:
    section: int
    span: tuple[int, int]
    quote: str
    nucleus: str | None
    qualifiers: tuple[tuple[str, str], ...]


@dataclass
class Admission:
    structured: list[Admitted] = field(default_factory=list)
    fallback: list[Admitted] = field(default_factory=list)
    rejections: list[tuple[int, str]] = field(default_factory=list)


def _structure_ok(p: Proposal, quote: str) -> bool:
    if not isinstance(p.nucleus, str) or not p.nucleus.strip() or p.nucleus not in quote:
        return False
    if not isinstance(p.qualifiers, (list, tuple)):
        return False
    return all(isinstance(q, (list, tuple)) and len(q) == 2 and q[0] in QUALIFIER_TYPES and isinstance(q[1], str)
               and q[1].strip() and q[1] in quote for q in p.qualifiers)


def admit_propositions(bundle: EvidenceBundle, proposals: list[Proposal]) -> Admission:
    """Admit `proposals` against `bundle` (see Notes for the rules)."""
    out, spans, pending_fallback = Admission(), set(), []
    for i, p in enumerate(proposals):
        if not isinstance(p, Proposal) or type(p.section) is not int:
            out.rejections.append((i, "malformed"))
            continue
        if not 0 <= p.section < len(bundle.sections):
            out.rejections.append((i, "bad_section"))
            continue
        sec = bundle.sections[p.section]
        if not sec.authoritative:
            out.rejections.append((i, "non_authoritative"))
            continue
        if not isinstance(p.quote, str) or not p.quote.strip():
            out.rejections.append((i, "empty_quote"))
            continue
        count = sec.text.count(p.quote)
        if count == 0:
            out.rejections.append((i, "quote_not_in_source"))
            continue
        if count > 1:
            out.rejections.append((i, "quote_ambiguous_in_source"))
            continue
        start = sec.text.index(p.quote)
        key = (p.section, start, start + len(p.quote))
        if key in spans:
            out.rejections.append((i, "duplicate_span"))
            continue
        spans.add(key)
        if _structure_ok(p, p.quote):
            out.structured.append(Admitted(p.section, key[1:], p.quote, p.nucleus, tuple((q[0], q[1]) for q in p.qualifiers)))
        else:
            pending_fallback.append(Admitted(p.section, key[1:], p.quote, None, ()))
    for f in pending_fallback:
        covered = any(a.section == f.section and a.span[0] <= f.span[0] and f.span[1] <= a.span[1] for a in out.structured)
        if not covered:
            out.fallback.append(f)
    return out
