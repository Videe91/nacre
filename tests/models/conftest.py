"""Model-layer fixtures: an org with two project scopes, a source-event writer, a policy setter, and a network block."""
import socket
import uuid

import pytest

from model_fakes import MODEL
from nacre.core.event import ActorKind, EventType, PayloadType, Source
from nacre.ledger.append_event import AppendRequest, Authorship, append_event
from nacre.models.set_model_policy import set_model_policy
from nacre.scopes.register_scope import ScopeKind, register_scope
from nacre.scopes.set_access import set_access


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
