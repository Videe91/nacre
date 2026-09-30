"""Tests for scopes/set_access.py (D-0005): grant, change, revoke through events; latest wins; org integrity."""
import uuid

import psycopg
import pytest

from nacre.ledger.append_event import AppendError
from nacre.scopes.register_scope import ScopeKind, register_scope
from nacre.scopes.set_access import set_access


def _project(open_, provider, org_id, owner):
    proj = uuid.uuid4()
    with open_(owner) as s:
        register_scope(s, provider, org_id=org_id, stream_id=proj, kind=ScopeKind.PROJECT, idempotency_key=str(uuid.uuid4()))
    return proj


def _set(open_, provider, org_id, owner, principal, stream, read, append):
    with open_(owner) as s:
        return set_access(s, provider, org_id=org_id, principal_id=principal, stream_id=stream, can_read=read,
                          can_append=append, idempotency_key=str(uuid.uuid4()))


def test_grant_change_and_revoke_take_effect_in_the_next_session(org, provider):
    org_id, owner, open_ = org
    proj, dev = _project(open_, provider, org_id, owner), uuid.uuid4()
    _set(open_, provider, org_id, owner, dev, proj, True, True)
    with open_(dev) as s:
        assert s.access.read_streams == {proj} and s.access.write_streams == {proj}
    _set(open_, provider, org_id, owner, dev, proj, True, False)
    with open_(dev) as s:
        assert s.access.write_streams == frozenset()
    _set(open_, provider, org_id, owner, dev, proj, False, False)
    with open_(dev) as s:
        assert s.access.read_streams == frozenset()


def test_grant_row_references_its_event(org, provider, migrated_db):
    org_id, owner, open_ = org
    proj, dev = _project(open_, provider, org_id, owner), uuid.uuid4()
    r = _set(open_, provider, org_id, owner, dev, proj, True, False)
    with psycopg.connect(migrated_db["admin"]) as c:
        assert c.execute("SELECT source_event_id, source_seq FROM scopes.scope_grants WHERE principal_id = %s",
                         (dev,)).fetchone() == (r.envelope.event_id, r.envelope.commit_seq)


def test_append_implies_read(org, provider):
    org_id, owner, open_ = org
    proj = _project(open_, provider, org_id, owner)
    with pytest.raises(AppendError, match="append implies read"):
        _set(open_, provider, org_id, owner, uuid.uuid4(), proj, False, True)


def test_cannot_grant_a_stream_of_another_org(org, provider, migrated_db):
    from nacre.core.db import DbRole, connect
    from nacre.scopes.bootstrap_org import bootstrap_org
    org_id, owner, open_ = org
    with connect(DbRole.MIGRATOR, dsn=migrated_db["admin"]) as admin:
        other_org = bootstrap_org(admin, provider, owner_principal_id=uuid.uuid4(), idempotency_key=str(uuid.uuid4()))
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _set(open_, provider, org_id, owner, uuid.uuid4(), other_org, True, False)


def test_only_org_appenders_can_grant(org, provider):
    org_id, owner, open_ = org
    proj = _project(open_, provider, org_id, owner)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _set(open_, provider, org_id, uuid.uuid4(), uuid.uuid4(), proj, True, True)
