"""Tests for sleep/build_evidence_bundle.py: authority from the envelope, rendering, errors."""
import uuid

import pytest

from nacre.eval.load_mnexa_family import load_mnexa_family
from nacre.sleep.build_evidence_bundle import BundleError, build_evidence_bundle


def test_the_bundle_marks_only_the_correction_authoritative_and_labels_the_decision_as_history(world, provider, family):
    with world["session"]() as s:
        lf = load_mnexa_family(s, provider, world["proj"], family, 14)
        b = build_evidence_bundle(s, provider, world["proj"], lf.outcome_id)
    assert [x.authoritative for x in b.sections] == [False, False, True, False, False]
    text = b.render()
    assert "NOT evidence of truth" in text and "[S2] role=correction (AUTHORITATIVE)" in text
    assert "<<<" not in text and b.source_event_ids == (lf.decision_id, lf.outcome_id)


def test_only_outcomes_have_bundles(world, provider, family):
    with world["session"]() as s:
        lf = load_mnexa_family(s, provider, world["proj"], family, 14)
        with pytest.raises(BundleError):
            build_evidence_bundle(s, provider, world["proj"], lf.decision_id)
        with pytest.raises(BundleError):
            build_evidence_bundle(s, provider, world["proj"], uuid.uuid4())
