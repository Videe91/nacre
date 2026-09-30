"""Ledger fixtures: a stream with events, a checkpoint signer, and the witness file."""
import uuid

import pytest

from nacre.core.event import ActorKind, EventType, PayloadType, Source
from nacre.ledger.append_event import AppendRequest, append_event
from nacre.ledger.write_checkpoint import LocalFileSigner


@pytest.fixture
def filled(session, streams, provider):
    """filled(n) appends n events to stream 'a' and returns their envelopes."""
    principal = uuid.uuid4()

    def fill(n, content="x"):
        out = []
        with session(principal, read=[streams["a"]], write=[streams["a"]]) as s:
            for i in range(n):
                out.append(append_event(s, provider, AppendRequest(
                    stream_id=streams["a"], event_type=EventType.MESSAGE, payload_type=PayloadType.TEXT,
                    actor_kind=ActorKind.AGENT, actor_id=uuid.UUID(int=1), source=Source.CHAT,
                    idempotency_key=str(uuid.uuid4()), content=f"{content}{i}")).envelope)
        return out
    return fill


@pytest.fixture
def signer(tmp_path):
    return LocalFileSigner.initialise(tmp_path / "ckpt" / "signing.key")


@pytest.fixture
def witness(tmp_path):
    return tmp_path / "witness.jsonl"
