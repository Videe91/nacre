"""EXP-0003 harness test helpers (uniquely named): a smart fake model and an org world with one scope per family."""
import json
import re
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field

from nacre.core.model_provider import ModelResponse, Usage
from nacre.models.set_model_policy import set_model_policy
from nacre.scopes.register_scope import ScopeKind, register_scope
from nacre.scopes.set_access import set_access

MODEL = "gpt-4o-mini-2024-07-18"


@dataclass
class SmartFake:
    """Sleep seats: quote the AUTHORITATIVE section exactly. Transfer: echo the memory block (or '(none)')."""
    name: str = "openai"
    replay: bool = False
    calls: list = field(default_factory=list)

    def complete(self, request, *, timeout_s):
        self.calls.append(request.purpose)
        text = request.messages[0].content
        if request.purpose.startswith("sleep."):
            m = re.search(r"\[S(\d+)\] role=\w+ \(AUTHORITATIVE\)\n(.*?)\n(?:\n|$)", text, re.S)
            items = [] if not m else [{"section": int(m.group(1)), "quote": m.group(2), "nucleus": m.group(2)[:24], "qualifiers": []}]
            out = json.dumps({"propositions": items})
        else:
            out = text.split("Relevant accumulated experience from MNEXA:\n", 1)[1].split("\n\nGive the action", 1)[0]
        return ModelResponse(out, "completed", Usage(900, 60, None), "r", MODEL, 3)


def world(org, provider):
    org_id, owner, open_ = org
    with open_(owner) as s:
        set_model_policy(s, provider, org_id=org_id, allowed=[("openai", MODEL)], idempotency_key=str(uuid.uuid4()))

    def new_scope(principal=owner, also=()):
        stream = uuid.uuid4()
        with open_(owner) as s:
            register_scope(s, provider, org_id=org_id, stream_id=stream, kind=ScopeKind.PROJECT, idempotency_key=str(uuid.uuid4()))
        for p in dict.fromkeys((principal, *also)):
            with open_(owner) as s:
                set_access(s, provider, org_id=org_id, principal_id=p, stream_id=stream, can_read=True, can_append=True,
                           idempotency_key=str(uuid.uuid4()))
        return stream

    @contextmanager
    def session():
        with open_(owner) as s:
            yield s
    return dict(org=org_id, owner=owner, open=open_, session=session, new_scope=new_scope)
