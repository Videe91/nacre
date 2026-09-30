"""Tests for ledger/replay_cycle.py: one cycle, commit order, watermark, causal links."""
import uuid

import pytest

from nacre.core.event import ActorKind, EventType, PayloadType, Source
from nacre.ledger.append_event import AppendRequest, append_event
from nacre.ledger.read_stream import ReadError
from nacre.ledger.replay_cycle import replay_cycle


def _append(s, provider, stream, cycle, event_type=EventType.ACTION, caused_by=None, content="x"):
    return append_event(s, provider, AppendRequest(
        stream_id=stream, event_type=event_type, payload_type=PayloadType.TEXT, actor_kind=ActorKind.AGENT,
        actor_id=uuid.UUID(int=5), source=Source.TOOL, idempotency_key=str(uuid.uuid4()), content=content,
        cycle_id=cycle, caused_by=caused_by)).envelope


@pytest.fixture
def rw(session, streams):
    p = uuid.uuid4()
    return lambda: session(p, read=[streams["a"]], write=[streams["a"]])


def test_replays_only_the_cycle_in_commit_order_with_causes(rw, provider, streams):
    a, c1, c2 = streams["a"], uuid.uuid4(), uuid.uuid4()
    with rw() as s:
        decision = _append(s, provider, a, c1, EventType.DECISION, content="run tests")
        _append(s, provider, a, c2, content="other cycle")
        action = _append(s, provider, a, c1, EventType.ACTION, caused_by=decision.event_id, content="pytest")
        _append(s, provider, a, c1, EventType.OUTCOME, caused_by=action.event_id, content="green")
    with rw() as s:
        replay = replay_cycle(s, provider, a, c1)
    assert [e.body["content"] for e in replay] == ["run tests", "pytest", "green"]
    assert [e.envelope.event_type for e in replay] == [EventType.DECISION, EventType.ACTION, EventType.OUTCOME]


def test_replay_as_of_a_watermark_sees_only_what_was_committed_then(rw, provider, streams):
    a, c = streams["a"], uuid.uuid4()
    with rw() as s:
        first = _append(s, provider, a, c, content="before")
    with rw() as s:
        _append(s, provider, a, c, content="after")
        assert [e.body["content"] for e in replay_cycle(s, provider, a, c, as_of=first.commit_seq)] == ["before"]


def test_unknown_cycle_is_empty_and_unreadable_stream_is_refused(rw, provider, streams, session):
    with rw() as s:
        _append(s, provider, streams["a"], uuid.uuid4())
        assert replay_cycle(s, provider, streams["a"], uuid.uuid4()) == []
    with session(uuid.uuid4(), read=[streams["b"]]) as s, pytest.raises(ReadError):
        replay_cycle(s, provider, streams["a"], uuid.uuid4())
