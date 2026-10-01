"""Tests for capture/record_action.py (D-0018)."""
import pytest

from capture_kit import AGENT, decision, k
from nacre.capture.record_action import record_action
from nacre.capture.record_decision import CaptureError
from nacre.capture.validate_refs import RefError
from nacre.core.event import ActorKind, Source
from nacre.ledger.append_event import Authorship
from nacre.ledger.read_stream import read_stream

A = dict(actor_kind=ActorKind.TOOL, actor_id=AGENT, source=Source.TOOL, authorship=Authorship.EXTERNAL)


def test_an_action_executes_a_decision_and_is_always_dispatched(rw, provider, streams):
    with rw() as s:
        d = decision(s, provider, streams["a"])
        a = record_action(s, provider, stream_id=streams["a"], idempotency_key=k(), decision_id=d.event_id,
                          action_kind="deploy", description="rolled out v2", stakes=("production",), **A).envelope
        (e,) = [x for x in read_stream(s, provider, streams["a"]) if x.envelope.event_id == a.event_id]
    assert e.body["content"]["dispatched"] is True and a.caused_by == d.event_id


def test_actions_need_a_decision_and_text(rw, provider, streams):
    with rw() as s:
        d = decision(s, provider, streams["a"])
        with pytest.raises(CaptureError):
            record_action(s, provider, stream_id=streams["a"], idempotency_key=k(), decision_id=d.event_id,
                          action_kind="deploy", description=" ", **A)
        a = record_action(s, provider, stream_id=streams["a"], idempotency_key=k(), decision_id=d.event_id,
                          action_kind="deploy", description="x", **A).envelope
        with pytest.raises(RefError):
            record_action(s, provider, stream_id=streams["a"], idempotency_key=k(), decision_id=a.event_id,
                          action_kind="deploy", description="x", **A)
