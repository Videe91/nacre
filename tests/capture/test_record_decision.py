"""Tests for capture/record_decision.py (D-0018)."""
import pytest

from capture_kit import decision
from nacre.capture.record_decision import CaptureError
from nacre.core.event import EventType
from nacre.ledger.read_stream import read_stream


def test_a_decision_is_recorded_with_its_body(rw, provider, streams):
    with rw() as s:
        d = decision(s, provider, streams["a"], stakes=("production",))
        (e,) = [x for x in read_stream(s, provider, streams["a"]) if x.envelope.event_id == d.event_id]
    c = e.body["content"]
    assert d.event_type == EventType.DECISION and c["reasoning_owner"] == "external" and c["stakes"] == ["production"]
    assert c["decided_from"] is None and c["refs"] == []


def test_decided_from_must_be_an_earlier_event_of_the_stream(rw, provider, streams):
    with rw() as s:
        first = decision(s, provider, streams["a"])
        second = decision(s, provider, streams["a"], decided_from=first.event_id, context_evidence_sha256="a" * 64)
        assert second.caused_by == first.event_id
        with pytest.raises(CaptureError, match="committed event"):
            decision(s, provider, streams["a"], decided_from=__import__("uuid").uuid4())


@pytest.mark.parametrize("kw", [{"text": " "}, {"stakes": ("reputation",)}, {"stakes": ("money", "money")},
                                {"context_evidence_sha256": "a" * 64}])
def test_invalid_decisions_are_refused(rw, provider, streams, kw):
    with rw() as s, pytest.raises(CaptureError):
        decision(s, provider, streams["a"], **kw)
