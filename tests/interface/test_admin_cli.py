"""Tests for interface/admin_cli.py (R4, D-0026 + amendments 1-2): every change is an auth row AND a config_event in
the org stream, atomically; the token is returned once and appears in no event; the role is checked; revocations."""
import os
import uuid

import psycopg
import pytest

from nacre.core.db import DbRole, connect
from nacre.interface.admin_cli import AdminError, AdminOps
from nacre.interface.authenticate_principal import AuthError, authenticate_principal
from nacre.ledger.read_stream import read_stream
from nacre.scopes.open_scoped_session import open_scoped_session

KEY = os.urandom(32)


@pytest.fixture
def ops(migrated_db, provider, streams):
    with connect(DbRole.PRINCIPAL_ADMIN, dsn=migrated_db["principal_admin"]) as conn:
        yield AdminOps(conn, provider, KEY, uuid.uuid4(), streams["org"])


def _org_events(session, provider, streams):
    reader = uuid.uuid4()
    with session(reader, read=[streams["org"]]) as s:
        return [e.body["content"] for e in read_stream(s, provider, streams["org"])]


def test_each_change_writes_its_row_and_its_config_event_together(ops, session, provider, streams, migrated_db):
    agent = ops.create_principal("agent", "build-bot")
    person = ops.create_principal("person", "reviewer")
    token = ops.issue_token(agent, days=30)
    did = ops.grant_delegation(agent, person, [streams["a"]], days=7)
    gid = ops.grant_reviewer(person, streams["a"], days=7)
    ops.revoke_delegation(did)
    ops.revoke_reviewer(gid)
    events = _org_events(session, provider, streams)
    assert [e["op"] for e in events] == ["principal_created", "principal_created", "token_issued", "delegation_granted",
                                         "reviewer_granted", "delegation_revoked", "reviewer_revoked"]
    assert token not in repr(events) and token.split("_")[3] not in repr(events)       # never the token or secret
    with psycopg.connect(migrated_db["admin"]) as c:
        for table, col in (("principals", "principal_id"), ("delegations", "delegation_id"), ("reviewer_grants", "grant_id")):
            assert c.execute(f"SELECT count(*) FROM auth.{table} WHERE config_event_id IS NULL").fetchone()[0] == 0


def test_an_issued_token_authenticates_until_it_is_revoked(ops, migrated_db):
    pid = ops.create_principal("agent", "bot")
    token = ops.issue_token(pid, days=1)
    with psycopg.connect(migrated_db["auth"]) as auth:
        p = authenticate_principal(auth, KEY, token)
        assert p.principal_id == pid
        ops.revoke_token(p.token_id)
        with pytest.raises(AuthError, match="revoked"):
            authenticate_principal(auth, KEY, token)


def test_a_failed_change_records_nothing(ops, session, provider, streams):
    agent = ops.create_principal("agent", "bot")
    with pytest.raises(psycopg.errors.RaiseException):
        ops.grant_reviewer(agent, streams["a"], days=7)                # reviewer grants go to persons only
    assert [e["op"] for e in _org_events(session, provider, streams)] == ["principal_created"]


def test_revoking_twice_is_refused_and_disable_blocks_authentication(ops, migrated_db):
    pid = ops.create_principal("agent", "bot")
    token = ops.issue_token(pid, days=1)
    ops.disable_principal(pid, "left the team")
    with pytest.raises(AdminError):
        ops.disable_principal(pid, "again")
    with psycopg.connect(migrated_db["auth"]) as auth, pytest.raises(AuthError, match="disabled"):
        authenticate_principal(auth, KEY, token)


def test_only_the_principal_admin_role_may_administer(migrated_db, provider, streams):
    with connect(DbRole.APP, dsn=migrated_db["app"]) as conn, pytest.raises(AdminError, match="nacre_principal_admin"):
        AdminOps(conn, provider, KEY, uuid.uuid4(), streams["org"])
