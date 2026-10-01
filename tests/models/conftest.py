"""Model-layer fixtures: an org with a project scope, a source event, and a scripted fake provider."""
import socket
import uuid
from dataclasses import dataclass, field

import pytest

from nacre.core.event import ActorKind, EventType, PayloadType, Source
from nacre.core.model_provider import Message, ModelParams, ModelRequest, ModelResponse, ProviderError, Usage
from nacre.ledger.append_event import AppendRequest, Authorship, append_event
from nacre.models.set_model_policy import set_model_policy
from nacre.scopes.register_scope import ScopeKind, register_scope
from nacre.scopes.set_access import set_access

MODEL = "gpt-4o-mini-2024-07-18"


@dataclass
class FakeProvider:
    script: list = field(default_factory=list)       # ModelResponse or ProviderError per call
    name: str = "openai"
    replay: bool = False
    calls: list = field(default_factory=list)

    def complete(self, request, *, timeout_s):
        self.calls.append((request, timeout_s))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def ok(text="the lesson", inp=1000, out=500, rid="resp_1"):
    return ModelResponse(text=text, finish_reason="stop", usage=Usage(inp, out, None), response_id=rid,
                         model_reported=MODEL, latency_ms=12)


def transient():
    return ProviderError("rate limited", retryable=True, error_class="rate_limit")


def req(content="Summarise the correction.", model=MODEL, provider="openai"):
    return ModelRequest(provider=provider, model=model, messages=(Message("user", content),),
                        params=ModelParams(max_tokens=256), purpose="test.seat")


@pytest.fixture
def world(org, provider):
    org_id, owner, open_ = org
    proj, other = uuid.uuid4(), uuid.uuid4()
    with open_(owner) as s:
        for st in (proj, other):
            register_scope(s, provider, org_id=org_id, stream_id=st, kind=ScopeKind.PROJECT, idempotency_key=str(uuid.uuid4()))
    with open_(owner) as s:
        for st in (proj, other):
            set_access(s, provider, org_id=org_id, principal_id=owner, stream_id=st, can_read=True, can_append=True,
                       idempotency_key=str(uuid.uuid4()))

    def source(stream=proj, text="CI says: retry with backoff"):
        with open_(owner) as s:
            return append_event(s, provider, AppendRequest(
                stream_id=stream, event_type=EventType.STATEMENT, payload_type=PayloadType.TEXT,
                actor_kind=ActorKind.PERSON, actor_id=owner, source=Source.CHAT, authorship=Authorship.SCOPE_PRINCIPAL,
                idempotency_key=str(uuid.uuid4()), content=text)).envelope.event_id

    def allow(*models):
        with open_(owner) as s:
            set_model_policy(s, provider, org_id=org_id, allowed=[("openai", m) for m in models],
                             idempotency_key=str(uuid.uuid4()))
    return dict(org=org_id, owner=owner, open=open_, proj=proj, other=other, source=source, allow=allow)


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("network access attempted in recorded mode (SI-6)")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
