"""Tests for capture/record_prediction.py (D-0018)."""
import pytest

from capture_kit import AGENT, decision, k
from nacre.capture.record_decision import CaptureError
from nacre.capture.record_outcome import Section, record_outcome
from nacre.capture.record_prediction import record_prediction
from nacre.capture.validate_refs import RefError
from nacre.core.event import ActorKind, Source
from nacre.ledger.append_event import Authorship

A = dict(actor_kind=ActorKind.AGENT, actor_id=AGENT, source=Source.CHAT, authorship=Authorship.SCOPE_PRINCIPAL)


def test_a_prediction_responds_to_a_decision(rw, provider, streams):
    with rw() as s:
        d = decision(s, provider, streams["a"])
        p = record_prediction(s, provider, stream_id=streams["a"], idempotency_key=k(), decision_id=d.event_id,
                              expected_outcome="tests pass", expected_success=True, confidence_pct=80, **A).envelope
        assert p.caused_by == d.event_id


@pytest.mark.parametrize("kw", [{"expected_outcome": ""}, {"predictor": "oracle"}, {"confidence_pct": 101},
                                {"confidence_pct": 0.5}, {"expected_success": "yes"}])
def test_invalid_predictions_are_refused(rw, provider, streams, kw):
    with rw() as s:
        d = decision(s, provider, streams["a"])
        args = dict(stream_id=streams["a"], idempotency_key=k(), decision_id=d.event_id, expected_outcome="x",
                    expected_success=True, **A)
        args.update(kw)
        with pytest.raises(CaptureError):
            record_prediction(s, provider, **args)


def test_a_prediction_cannot_respond_to_an_outcome(rw, provider, streams):
    with rw() as s:
        d = decision(s, provider, streams["a"])
        o = record_outcome(s, provider, stream_id=streams["a"], idempotency_key=k(), outcome_for=d.event_id,
                           success=True, sections=(Section("status", "ok"),), **A).envelope
        with pytest.raises(RefError):
            record_prediction(s, provider, stream_id=streams["a"], idempotency_key=k(), decision_id=o.event_id,
                              expected_outcome="x", expected_success=True, **A)
