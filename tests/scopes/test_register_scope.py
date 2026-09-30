"""Tests for scopes/register_scope.py (D-0005): event + projection row, atomically; retries; authority."""
import uuid

import psycopg
import pytest

from nacre.ledger.append_event import AppendError, IdempotencyConflict
from nacre.scopes.register_scope import ScopeKind, register_scope


def test_registers_event_and_row_together(org, provider, migrated_db):
    org_id, owner, open_ = org
    proj = uuid.uuid4()
    with open_(owner) as s:
        r = register_scope(s, provider, org_id=org_id, stream_id=proj, kind=ScopeKind.PROJECT,
                           idempotency_key=str(uuid.uuid4()))
    assert r.created and r.envelope.stream_id == org_id and r.envelope.commit_seq == 2
    with psycopg.connect(migrated_db["admin"]) as c:
        assert c.execute("SELECT kind, org_id, source_event_id FROM scopes.scopes WHERE stream_id = %s", (proj,)).fetchone() \
            == ("project", org_id, r.envelope.event_id)


def test_exact_retry_writes_no_second_row(org, provider, migrated_db):
    org_id, owner, open_ = org
    proj, key = uuid.uuid4(), str(uuid.uuid4())
    for _ in range(2):
        with open_(owner) as s:
            last = register_scope(s, provider, org_id=org_id, stream_id=proj, kind=ScopeKind.PROJECT, idempotency_key=key)
    assert last.created is False
    with psycopg.connect(migrated_db["admin"]) as c:
        assert c.execute("SELECT count(*) FROM scopes.scopes WHERE stream_id = %s", (proj,)).fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM ledger.events WHERE stream_id = %s", (org_id,)).fetchone()[0] == 2


def test_same_key_for_a_different_scope_conflicts(org, provider):
    org_id, owner, open_ = org
    key = str(uuid.uuid4())
    with open_(owner) as s:
        register_scope(s, provider, org_id=org_id, stream_id=uuid.uuid4(), kind=ScopeKind.TEAM, idempotency_key=key)
    with open_(owner) as s, pytest.raises(IdempotencyConflict):
        register_scope(s, provider, org_id=org_id, stream_id=uuid.uuid4(), kind=ScopeKind.TEAM, idempotency_key=key)


def test_org_kind_and_reused_ids_are_refused(org, provider):
    org_id, owner, open_ = org
    with open_(owner) as s, pytest.raises(AppendError, match="ScopeKind"):
        register_scope(s, provider, org_id=org_id, stream_id=uuid.uuid4(), kind="org", idempotency_key=str(uuid.uuid4()))
    with open_(owner) as s, pytest.raises(AppendError, match="distinct"):
        register_scope(s, provider, org_id=org_id, stream_id=org_id, kind=ScopeKind.USER, idempotency_key=str(uuid.uuid4()))


def test_a_principal_without_org_append_cannot_register(org, provider):
    org_id, _, open_ = org
    with open_(uuid.uuid4()) as s, pytest.raises(psycopg.errors.InsufficientPrivilege):
        register_scope(s, provider, org_id=org_id, stream_id=uuid.uuid4(), kind=ScopeKind.PROJECT,
                       idempotency_key=str(uuid.uuid4()))
