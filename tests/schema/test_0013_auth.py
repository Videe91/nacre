"""Tests for migration 0013 (D-0026 + amendments 1-2): the auth tables, the three roles' exact powers, keyed-hash-only
tokens, 90-day maxima, kind checks, no deletes, and the app seeing only its own delegations and reviewer grants."""
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

NOW = datetime.now(UTC)


def _principal(c, kind="agent", org=None, sources=()):
    pid = uuid.uuid4()
    c.execute("INSERT INTO auth.principals (principal_id, org_id, kind, display_name, service_sources, config_event_id) "
              "VALUES (%s, %s, %s, %s, %s, %s)", (pid, org or uuid.uuid4(), kind, f"{kind}-x", list(sources), uuid.uuid4()))
    return pid


def _token(c, pid, days=30):
    tid = uuid.uuid4()
    c.execute("INSERT INTO auth.tokens (token_id, principal_id, token_mac, expires_at) VALUES (%s, %s, %s, %s)",
              (tid, pid, uuid.uuid4().bytes * 2, NOW + timedelta(days=days)))
    return tid


@pytest.fixture
def admin(migrated_db):
    with psycopg.connect(migrated_db["principal_admin"], autocommit=True) as c:
        yield c


def test_tables_hold_no_secret_column(migrated_db):
    with psycopg.connect(migrated_db["admin"]) as c:
        cols = {r[0] for r in c.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = 'auth'")}
    assert not cols & {"secret", "token", "token_secret", "password", "plaintext"} and "token_mac" in cols


def test_principal_admin_can_create_and_revoke_but_never_delete(admin):
    pid = _principal(admin)
    tid = _token(admin, pid)
    admin.execute("UPDATE auth.tokens SET revoked_at = now() WHERE token_id = %s", (tid,))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        admin.execute("UPDATE auth.tokens SET token_mac = %s WHERE token_id = %s", (b"\x00" * 32, tid))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        admin.execute("DELETE FROM auth.tokens WHERE token_id = %s", (tid,))


def test_auth_role_reads_and_stamps_last_used_only(admin, migrated_db):
    pid = _principal(admin)
    tid = _token(admin, pid)
    with psycopg.connect(migrated_db["auth"], autocommit=True) as c:
        assert c.execute("SELECT principal_id FROM auth.tokens WHERE token_id = %s", (tid,)).fetchone()[0] == pid
        c.execute("UPDATE auth.tokens SET last_used_at = now() WHERE token_id = %s", (tid,))
        for sql in ("UPDATE auth.tokens SET revoked_at = NULL", "INSERT INTO auth.principals (principal_id, org_id, kind, "
                    "display_name, config_event_id) VALUES (gen_random_uuid(), gen_random_uuid(), 'agent', 'x', gen_random_uuid())",
                    "SELECT 1 FROM ledger.events", "SELECT 1 FROM keys.data_keys", "SELECT 1 FROM auth.delegations"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(sql)


def test_90_day_maximum_and_kind_checks(admin):
    agent, person = _principal(admin, "agent"), _principal(admin, "person")
    with pytest.raises(psycopg.errors.CheckViolation):
        _token(admin, agent, days=91)
    with pytest.raises(psycopg.errors.RaiseException):
        admin.execute("INSERT INTO auth.delegations (delegation_id, agent_principal, person_principal, streams, expires_at, "
                      "config_event_id) VALUES (%s, %s, %s, %s, %s, %s)",
                      (uuid.uuid4(), person, agent, [uuid.uuid4()], NOW + timedelta(days=1), uuid.uuid4()))
    with pytest.raises(psycopg.errors.RaiseException):
        admin.execute("INSERT INTO auth.reviewer_grants (grant_id, person_principal, stream_id, expires_at, config_event_id) "
                      "VALUES (%s, %s, %s, %s, %s)", (uuid.uuid4(), agent, uuid.uuid4(), NOW + timedelta(days=1), uuid.uuid4()))
    with pytest.raises(psycopg.errors.CheckViolation):
        _principal(admin, "agent", sources=["ci"])                     # only services carry sources


def test_the_app_sees_only_its_own_delegations_and_reviewer_grants(admin, migrated_db):
    agent, other_agent, person = _principal(admin, "agent"), _principal(admin, "agent"), _principal(admin, "person")
    for a in (agent, other_agent):
        admin.execute("INSERT INTO auth.delegations (delegation_id, agent_principal, person_principal, streams, expires_at, "
                      "config_event_id) VALUES (%s, %s, %s, %s, %s, %s)",
                      (uuid.uuid4(), a, person, [uuid.uuid4()], NOW + timedelta(days=1), uuid.uuid4()))
    admin.execute("INSERT INTO auth.reviewer_grants (grant_id, person_principal, stream_id, expires_at, config_event_id) "
                  "VALUES (%s, %s, %s, %s, %s)", (uuid.uuid4(), person, uuid.uuid4(), NOW + timedelta(days=1), uuid.uuid4()))
    with psycopg.connect(migrated_db["app"]) as c:
        c.execute("SELECT set_config('nacre.principal', %s, true)", (str(agent),))
        assert [r[0] for r in c.execute("SELECT agent_principal FROM auth.delegations")] == [agent]
        assert c.execute("SELECT count(*) FROM auth.reviewer_grants").fetchone()[0] == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("SELECT 1 FROM auth.tokens")
    with psycopg.connect(migrated_db["app"]) as c:
        c.execute("SELECT set_config('nacre.principal', %s, true)", (str(person),))
        assert c.execute("SELECT count(*) FROM auth.reviewer_grants").fetchone()[0] == 1
