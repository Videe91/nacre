"""Capture test helpers (a uniquely named module, never imported as `conftest`)."""
import uuid

from nacre.capture.record_decision import record_decision
from nacre.core.event import ActorKind, Source
from nacre.ledger.append_event import Authorship

AGENT = uuid.UUID(int=77)


def k():
    return str(uuid.uuid4())


def decision(s, provider, stream, text="ship the hotfix", **kw):
    args = dict(stream_id=stream, actor_kind=ActorKind.AGENT, actor_id=AGENT, source=Source.CHAT,
                authorship=Authorship.SCOPE_PRINCIPAL, idempotency_key=k(), decision_text=text)
    args.update(kw)
    return record_decision(s, provider, **args).envelope
