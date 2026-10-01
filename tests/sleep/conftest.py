"""Sleep fixtures: an org with a project scope, an owner with access, and a policy allowing the pinned model."""
import json
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

from sleep_kit import MODEL
from nacre.models.set_model_policy import set_model_policy
from nacre.scopes.register_scope import ScopeKind, register_scope
from nacre.scopes.set_access import set_access

TASKS = Path(__file__).resolve().parents[2] / "tests" / "regression" / "mnexa" / "tasks"


@pytest.fixture
def world(org, provider):
    org_id, owner, open_ = org
    proj = uuid.uuid4()
    with open_(owner) as s:
        register_scope(s, provider, org_id=org_id, stream_id=proj, kind=ScopeKind.PROJECT, idempotency_key=str(uuid.uuid4()))
    with open_(owner) as s:
        set_access(s, provider, org_id=org_id, principal_id=owner, stream_id=proj, can_read=True, can_append=True,
                   idempotency_key=str(uuid.uuid4()))
    with open_(owner) as s:
        set_model_policy(s, provider, org_id=org_id, allowed=[("openai", MODEL)], idempotency_key=str(uuid.uuid4()))

    @contextmanager
    def session():
        with open_(owner) as s:
            yield s
    return dict(org=org_id, proj=proj, session=session)


@pytest.fixture
def family():
    return json.loads((TASKS / "tasks_014.json").read_text())["families"][0]
