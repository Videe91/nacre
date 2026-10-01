"""Tests for sleep/admit_propositions.py: every rule and rejection reason, fallback, closed world, guard (iii)."""
import uuid

import pytest

from nacre.sleep.admit_propositions import REJECTION_REASONS, Proposal, admit_propositions
from nacre.sleep.build_evidence_bundle import BundleSection, EvidenceBundle

RULE = "On VX-41, wait 137 ms, then retry once with header X-Relay: cobalt."


def bundle(authoritative=True):
    secs = (BundleSection(0, "status", "FAIL: VX-41", False), BundleSection(1, "correction", RULE, authoritative),
            BundleSection(2, "operator_note", "wait 137 ms seems slow", False))
    return EvidenceBundle(uuid.uuid4(), uuid.uuid4(), "retry immediately", False, secs, ())


def test_a_grounded_structured_proposition_is_admitted_with_its_span():
    a = admit_propositions(bundle(), [Proposal(1, RULE, "retry once", (("condition", "On VX-41"),))])
    (x,) = a.structured
    assert x.span == (0, len(RULE)) and x.nucleus == "retry once" and a.fallback == [] and a.rejections == []


@pytest.mark.parametrize("p,reason", [
    (Proposal(9, RULE, "retry"), "bad_section"),
    (Proposal(0, "FAIL: VX-41", "FAIL"), "non_authoritative"),
    (Proposal(2, "wait 137 ms seems slow", "wait"), "non_authoritative"),
    (Proposal(1, "  ", "x"), "empty_quote"),
    (Proposal(1, "wait 5 seconds and retry twice", "retry"), "quote_not_in_source"),     # fabricated support
    (Proposal("1", RULE, "retry"), "malformed"),
])
def test_hard_rejections_never_become_fallbacks(p, reason):
    a = admit_propositions(bundle(), [p])
    assert a.rejections == [(0, reason)] and a.structured == [] and a.fallback == [] and reason in REJECTION_REASONS


def test_ambiguous_quotes_and_duplicate_spans_are_rejected():
    twice = BundleSection(1, "correction", "retry once. retry once.", True)
    b = EvidenceBundle(uuid.uuid4(), uuid.uuid4(), "x", False, (twice,), ())
    assert admit_propositions(b, [Proposal(0, "retry once", "retry")]).rejections == [(0, "quote_ambiguous_in_source")]
    a = admit_propositions(bundle(), [Proposal(1, RULE, "retry once"), Proposal(1, RULE, "wait 137 ms")])
    assert len(a.structured) == 1 and a.rejections == [(1, "duplicate_span")]


@pytest.mark.parametrize("bad", [Proposal(1, RULE, "not in quote"), Proposal(1, RULE, ""),
                                 Proposal(1, RULE, "retry", (("timing", "137 ms"),)),
                                 Proposal(1, RULE, "retry", (("condition", "absent"),))])
def test_valid_support_with_broken_structure_becomes_a_fallback(bad):
    a = admit_propositions(bundle(), [bad])
    assert a.structured == [] and [(f.span, f.nucleus) for f in a.fallback] == [((0, len(RULE)), None)]


def test_a_fallback_covered_by_an_admitted_structure_is_dropped():
    part = "wait 137 ms"
    a = admit_propositions(bundle(), [Proposal(1, RULE, "retry once"), Proposal(1, part, "")])
    assert len(a.structured) == 1 and a.fallback == []


def test_guard_iii_when_every_section_is_untrusted_nothing_is_admitted():
    a = admit_propositions(bundle(authoritative=False), [Proposal(1, RULE, "retry once"), Proposal(1, RULE, "")])
    assert a.structured == [] and a.fallback == [] and {r for _, r in a.rejections} == {"non_authoritative"}
