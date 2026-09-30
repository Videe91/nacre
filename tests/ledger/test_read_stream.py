"""Tests for ledger/read_stream.py: commit order, AS_OF(N) stability (Phase 1 gate item 2), Shredded, isolation."""
import uuid

import psycopg
import pytest

from nacre.core.event import EventType, PayloadType, ActorKind, Source
from nacre.keys.decrypt_payload import Shredded
from nacre.ledger.append_event import AppendRequest, append_event
from nacre.ledger.read_stream import ReadError, head, read_stream


def _append(s, provider, stream, content):
    return append_event(s, provider, AppendRequest(
        stream_id=stream, event_type=EventType.MESSAGE, payload_type=PayloadType.TEXT, actor_kind=ActorKind.AGENT,
        actor_id=uuid.UUID(int=3), source=Source.CHAT, idempotency_key=str(uuid.uuid4()), content=content)).envelope


@pytest.fixture
def rw(session, streams):
    p = uuid.uuid4()
    return lambda: session(p, read=[streams["a"]], write=[streams["a"]])


def test_reads_in_commit_order_with_bodies(rw, provider, streams):
    a = streams["a"]
    with rw() as s:
        for i in range(5):
            _append(s, provider, a, f"m{i}")
    with rw() as s:
        events = read_stream(s, provider, a)
        assert head(s, a) == 5
    assert [e.envelope.commit_seq for e in events] == [1, 2, 3, 4, 5]
    assert [e.body["content"] for e in events] == [f"m{i}" for i in range(5)]


def test_from_seq_and_limit(rw, provider, streams):
    a = streams["a"]
    with rw() as s:
        for i in range(6):
            _append(s, provider, a, f"m{i}")
        assert [e.envelope.commit_seq for e in read_stream(s, provider, a, from_seq=3, limit=2)] == [3, 4]


def test_as_of_is_stable_after_later_commits(rw, provider, streams):
    # Gate item 2 (MNEXA ADR-0010 R-18): AS_OF(N), once exposed, is byte-identical after later commits.
    a = streams["a"]
    with rw() as s:
        for i in range(4):
            _append(s, provider, a, f"before-{i}")
    with rw() as s:
        snapshot = read_stream(s, provider, a, as_of=3)
    with rw() as s:
        for i in range(10):
            _append(s, provider, a, f"after-{i}")
    with rw() as s:
        again = read_stream(s, provider, a, as_of=3)
    assert again == snapshot and [e.envelope.commit_seq for e in again] == [1, 2, 3]


def test_as_of_beyond_the_head_is_refused(rw, provider, streams):
    a = streams["a"]
    with rw() as s:
        _append(s, provider, a, "only")
        with pytest.raises(ReadError, match="beyond the head"):
            read_stream(s, provider, a, as_of=2)
        assert read_stream(s, provider, a, as_of=0) == []


def test_unreadable_stream_is_refused_not_empty(rw, provider, streams, session):
    with rw() as s:
        _append(s, provider, streams["a"], "x")
    with session(uuid.uuid4(), read=[streams["b"]]) as s, pytest.raises(ReadError, match="not readable"):
        read_stream(s, provider, streams["a"])


def test_shredded_events_are_returned_as_shredded(rw, provider, streams):
    a = streams["a"]
    with rw() as s:
        env = _append(s, provider, a, "gone soon")
    with psycopg.connect(streams["dsn"]["admin"]) as c:
        c.execute("DELETE FROM keys.stream_master_keys WHERE stream_id = %s", (a,))
    with rw() as s:
        (event,) = read_stream(s, provider, a)
    assert event.envelope == env and event.body == Shredded(env.key_id)


def test_read_without_decrypt(rw, provider, streams):
    with rw() as s:
        _append(s, provider, streams["a"], "x")
        (e,) = read_stream(s, provider, streams["a"], decrypt=False)
    assert e.body is None
