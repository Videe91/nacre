"""Recall fixtures: one principal with read+append on stream a (stream b is another scope).
rw() opens a normal read-write session; snap() a recall snapshot session (REPEATABLE READ, read only) for the same
principal. Grants are recorded on the first rw()/grant() call."""
import uuid
from contextlib import contextmanager

import psycopg
import pytest

from nacre.core.db import DbRole, connect
from nacre.scopes.open_scoped_session import open_scoped_session

_seq = iter(range(10**6, 2 * 10**6))


def _grant(dsn, principal, stream, org, read=True, append=True):
    with psycopg.connect(dsn["admin"]) as admin:
        admin.execute("""INSERT INTO scopes.scope_grants
                         (principal_id, stream_id, org_id, can_read, can_append, source_event_id, source_seq)
                         VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                      (principal, stream, org, read, read and append, uuid.uuid4(), next(_seq)))


@pytest.fixture
def principal():
    return uuid.uuid4()


@pytest.fixture
def grant(streams):
    return lambda p, stream, read=True, append=True: _grant(streams["dsn"], p, stream, streams["org"], read, append)


@pytest.fixture
def rw(session, streams, principal):
    return lambda: session(principal, read=[streams["a"]], write=[streams["a"]])


@pytest.fixture
def snap(streams, principal):
    @contextmanager
    def _snap(who=None):
        with connect(DbRole.APP, dsn=streams["dsn"]["app"]) as conn, \
                open_scoped_session(conn, who or principal, snapshot=True) as s:
            yield s
    return _snap
