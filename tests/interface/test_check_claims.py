"""Tests for interface/check_claims.py (R3, D-0026 amendment 1): one test per claim-table row with its forbidden
neighbours; reviewer grants and delegations are live, revocable, time-limited and scoped; a delegation never lends
reviewer powers; corrections only from reviewer-granted persons or structured service results."""
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from nacre.interface.authenticate_principal import Principal
from nacre.interface.check_claims import ClaimRejected, Claims, check_claims

NOW = datetime.now(UTC)


@pytest.fixture
def admin(migrated_db):
    with psycopg.connect(migrated_db["principal_admin"], autocommit=True) as c:
        yield c


def _p(admin, kind, sources=()):
    pid = uuid.uuid4()
    admin.execute("INSERT INTO auth.principals (principal_id, org_id, kind, display_name, service_sources, config_event_id) "
                  "VALUES (%s, %s, %s, 'x', %s, %s)", (pid, uuid.uuid4(), kind, list(sources), uuid.uuid4()))
    return Principal(pid, uuid.uuid4(), kind, tuple(sources), uuid.uuid4())


def _check(session, streams, principal, *claim, structured=False, correction=False, on_behalf_of=None, now=NOW,
           stream=None):
    with session(principal.principal_id, read=[streams["a"]], write=[streams["a"]]) as s:
        return check_claims(s, principal, Claims(stream or streams["a"], *claim, structured, correction, on_behalf_of),
                            now=now)


def _rejected(*a, **k):
    with pytest.raises(ClaimRejected) as e:
        _check(*a, **k)
    return e.value.code


def test_agent_row(admin, session, streams):
    agent = _p(admin, "agent")
    for triple in (("chat", "external", "agent"), ("tool", "external", "agent")):
        v = _check(session, streams, agent, *triple)
        assert not v.authoritative_allowed
    for bad in (("chat", "scope_principal", "agent"), ("review", "scope_principal", "person"),
                ("ci", "integration_result", "system"), ("chat", "external", "person")):
        assert _rejected(session, streams, agent, *bad) == "forbidden_claim"
    assert _rejected(session, streams, agent, "chat", "external", "agent", correction=True) == "forbidden_claim"


def test_person_row_and_reviewer_grant_lifecycle(admin, session, streams):
    person = _p(admin, "person")
    assert not _check(session, streams, person, "chat", "scope_principal", "person").authoritative_allowed
    assert _rejected(session, streams, person, "review", "scope_principal", "person") == "forbidden_claim"
    assert _rejected(session, streams, person, "chat", "scope_principal", "person", correction=True) == "forbidden_claim"
    gid = uuid.uuid4()
    admin.execute("INSERT INTO auth.reviewer_grants (grant_id, person_principal, stream_id, expires_at, config_event_id) "
                  "VALUES (%s, %s, %s, %s, %s)", (gid, person.principal_id, streams["a"], NOW + timedelta(days=1), uuid.uuid4()))
    v = _check(session, streams, person, "review", "scope_principal", "person", correction=True)
    assert v.authoritative_allowed
    assert _rejected(session, streams, person, "review", "scope_principal", "person", correction=True,
                     now=NOW + timedelta(days=2)) == "forbidden_claim"          # expired
    admin.execute("UPDATE auth.reviewer_grants SET revoked_at = now() WHERE grant_id = %s", (gid,))
    assert _rejected(session, streams, person, "review", "scope_principal", "person", correction=True) == "forbidden_claim"


def test_service_row(admin, session, streams):
    ci = _p(admin, "service", sources=("ci",))
    v = _check(session, streams, ci, "ci", "integration_result", "system", structured=True, correction=True)
    assert v.authoritative_allowed
    assert _rejected(session, streams, ci, "ci", "integration_result", "system", structured=False) == "forbidden_claim"
    assert _rejected(session, streams, ci, "review", "integration_result", "system", structured=True) == "forbidden_claim"
    assert _rejected(session, streams, ci, "ci", "scope_principal", "system", structured=True) == "forbidden_claim"


def test_operator_row(admin, session, streams):
    op = _p(admin, "operator")
    assert not _check(session, streams, op, "system", "scope_principal", "system").authoritative_allowed
    assert _rejected(session, streams, op, "system", "scope_principal", "system", correction=True) == "forbidden_claim"
    assert _rejected(session, streams, op, "review", "scope_principal", "person") == "forbidden_claim"


def test_on_behalf_of_needs_a_live_scoped_delegation_and_never_lends_reviewer_powers(admin, session, streams):
    agent, person = _p(admin, "agent"), _p(admin, "person")
    assert _rejected(session, streams, agent, "chat", "external", "agent", on_behalf_of=person.principal_id) == \
        "forbidden_claim"
    did = uuid.uuid4()
    admin.execute("INSERT INTO auth.delegations (delegation_id, agent_principal, person_principal, streams, expires_at, "
                  "config_event_id) VALUES (%s, %s, %s, %s, %s, %s)",
                  (did, agent.principal_id, person.principal_id, [streams["a"]], NOW + timedelta(days=1), uuid.uuid4()))
    admin.execute("INSERT INTO auth.reviewer_grants (grant_id, person_principal, stream_id, expires_at, config_event_id) "
                  "VALUES (%s, %s, %s, %s, %s)", (uuid.uuid4(), person.principal_id, streams["a"], NOW + timedelta(days=1),
                                                  uuid.uuid4()))
    assert _check(session, streams, agent, "chat", "external", "agent", on_behalf_of=person.principal_id)
    assert _rejected(session, streams, agent, "chat", "external", "agent", correction=True,
                     on_behalf_of=person.principal_id) == "forbidden_claim"      # the person's grant is not lent
    assert _rejected(session, streams, person, "chat", "scope_principal", "person",
                     on_behalf_of=agent.principal_id) == "forbidden_claim"       # only agents delegate-act
    admin.execute("UPDATE auth.delegations SET revoked_at = now() WHERE delegation_id = %s", (did,))
    assert _rejected(session, streams, agent, "chat", "external", "agent", on_behalf_of=person.principal_id) == \
        "forbidden_claim"


def test_an_ungranted_stream_is_refused(admin, session, streams):
    agent = _p(admin, "agent")
    assert _rejected(session, streams, agent, "chat", "external", "agent", stream=streams["b"]) == "scope_not_granted"
