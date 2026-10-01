"""Tests for capture/record_correction.py (D-0018)."""
import pytest

from capture_kit import AGENT, decision, k
from nacre.capture.record_correction import record_correction
from nacre.capture.record_decision import CaptureError
from nacre.core.event import ActorKind, EventType, Source, Trust
from nacre.ledger.append_event import Authorship

PERSON = dict(actor_kind=ActorKind.PERSON, actor_id=AGENT, source=Source.CHAT, authorship=Authorship.SCOPE_PRINCIPAL)


def test_a_person_corrects_an_earlier_event(rw, provider, streams):
    with rw() as s:
        d = decision(s, provider, streams["a"])
        c = record_correction(s, provider, stream_id=streams["a"], idempotency_key=k(), correction_of=d.event_id,
                              text="we never deploy on Fridays", **PERSON).envelope
    assert c.event_type == EventType.CORRECTION and c.caused_by == d.event_id and c.trust == Trust.TRUSTED


def test_empty_corrections_are_refused(rw, provider, streams):
    with rw() as s:
        d = decision(s, provider, streams["a"])
        with pytest.raises(CaptureError):
            record_correction(s, provider, stream_id=streams["a"], idempotency_key=k(), correction_of=d.event_id,
                              text=" ", **PERSON)
