"""Tests for capture/validate_refs.py (D-0018; MNEXA ADR-0008 vocabulary)."""
import uuid

import pytest

from capture_kit import decision
from nacre.capture.validate_refs import RELATIONS, Ref, RefError, validate_refs


def test_valid_refs_are_returned_in_order(rw, provider, streams):
    with rw() as s:
        d = decision(s, provider, streams["a"])
        assert validate_refs(s, streams["a"], [Ref("outcome_for", d.event_id), Ref("continuation_of", d.event_id)]) == [
            {"rel": "outcome_for", "event_id": str(d.event_id)}, {"rel": "continuation_of", "event_id": str(d.event_id)}]


@pytest.mark.parametrize("rel", ["caused_by", "explains", "proves", "supports_truth_of", "contradicts_truth_of"])
def test_causal_and_truth_relations_are_not_in_the_vocabulary(rw, provider, streams, rel):
    assert rel not in RELATIONS
    with rw() as s:
        d = decision(s, provider, streams["a"])
        with pytest.raises(RefError, match="not an allowed"):
            validate_refs(s, streams["a"], [Ref(rel, d.event_id)])


def test_targets_must_exist_in_the_same_stream_and_have_the_right_type(rw, session, provider, streams):
    with session(uuid.uuid4(), read=[streams["b"]], write=[streams["b"]]) as sb:
        other = decision(sb, provider, streams["b"])
    with rw() as s:
        d = decision(s, provider, streams["a"])
        with pytest.raises(RefError, match="not a committed"):
            validate_refs(s, streams["a"], [Ref("outcome_for", other.event_id)])     # other stream
        with pytest.raises(RefError, match="not a committed"):
            validate_refs(s, streams["a"], [Ref("outcome_for", uuid.uuid4())])      # does not exist
        with pytest.raises(RefError, match="cannot point to a decision"):
            validate_refs(s, streams["a"], [Ref("evaluates_prediction", d.event_id)])
        with pytest.raises(RefError, match="duplicate"):
            validate_refs(s, streams["a"], [Ref("outcome_for", d.event_id)] * 2)
