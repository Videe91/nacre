"""Tests for scopes/resolve_access.py (D-0005)."""
import uuid

import psycopg
import pytest

from nacre.scopes.resolve_access import Access, AccessError, resolve_access


def _resolve(dsn, principal, scoped_as=None):
    with psycopg.connect(dsn) as conn:
        conn.execute("SELECT set_config('nacre.principal', %s, true)", (str(scoped_as or principal),))
        return resolve_access(conn, principal)


def test_no_grants_means_no_access(world):
    p = uuid.uuid4()
    assert _resolve(world["dsn"]["app"], p) == Access(p, frozenset(), frozenset())


def test_read_and_append_grants(world, grant):
    p = uuid.uuid4()
    proj, user = world["streams"]["project"][0], world["streams"]["user"][0]
    grant(p, proj, read=True, append=True)
    grant(p, user, read=True, append=False)
    a = _resolve(world["dsn"]["app"], p)
    assert a.read_streams == {proj, user} and a.write_streams == {proj}


def test_latest_grant_wins_and_revoke_removes(world, grant):
    p, proj = uuid.uuid4(), world["streams"]["project"][0]
    grant(p, proj, read=True, append=True)
    grant(p, proj, read=True, append=False)     # downgrade
    assert _resolve(world["dsn"]["app"], p).write_streams == frozenset()
    grant(p, proj, read=False, append=False)    # revoke
    assert _resolve(world["dsn"]["app"], p).read_streams == frozenset()
    grant(p, proj, read=True, append=True)      # re-grant
    assert _resolve(world["dsn"]["app"], p).write_streams == {proj}


def test_other_principals_grants_are_invisible(world, grant):
    p, other = uuid.uuid4(), uuid.uuid4()
    grant(other, world["streams"]["project"][0], read=True, append=True)
    assert _resolve(world["dsn"]["app"], p).read_streams == frozenset()


def test_no_inheritance_from_org(world, grant):
    p = uuid.uuid4()
    grant(p, world["org"], read=True, append=True)
    assert _resolve(world["dsn"]["app"], p).read_streams == {world["org"]}


def test_refuses_when_scoped_as_someone_else(world, grant):
    p, q = uuid.uuid4(), uuid.uuid4()
    grant(q, world["streams"]["project"][0])
    with pytest.raises(AccessError, match="nacre.principal"):
        _resolve(world["dsn"]["app"], q, scoped_as=p)


def test_refuses_when_principal_setting_is_missing(world):
    with psycopg.connect(world["dsn"]["app"]) as conn:
        with pytest.raises(AccessError):
            resolve_access(conn, uuid.uuid4())
