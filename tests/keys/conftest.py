"""Fixtures for key tests: a root-key provider, registered streams, and scoped sessions on them."""
import uuid
from contextlib import contextmanager
from itertools import count

import psycopg
import pytest

from nacre.core.db import DbRole, connect
from nacre.keys.local_file_root_key import LocalFileRootKeyProvider
from nacre.scopes.open_scoped_session import open_scoped_session

_seq = count(1)


@pytest.fixture
def provider(tmp_path):
    return LocalFileRootKeyProvider.initialise(tmp_path / "rootkeys")


@pytest.fixture
def streams(migrated_db):
    """An org and two project streams, registered (grants need registered scopes)."""
    org, a, b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with psycopg.connect(migrated_db["admin"]) as admin:
        for s, kind in ((org, "org"), (a, "project"), (b, "project")):
            admin.execute("INSERT INTO scopes.scopes (stream_id, kind, org_id, source_event_id) VALUES (%s, %s, %s, %s)",
                          (s, kind, org, uuid.uuid4()))
    return {"org": org, "a": a, "b": b, "dsn": migrated_db}


@pytest.fixture
def session(streams):
    """session(principal, read=[...], write=[...]) -> context manager yielding a ScopedSession."""
    @contextmanager
    def _session(principal, read=(), write=()):
        with psycopg.connect(streams["dsn"]["admin"]) as admin:
            for s in set(read) | set(write):
                admin.execute("""INSERT INTO scopes.scope_grants
                                 (principal_id, stream_id, org_id, can_read, can_append, source_event_id, source_seq)
                                 VALUES (%s, %s, %s, true, %s, %s, %s)""",
                              (principal, s, streams["org"], s in write, uuid.uuid4(), next(_seq)))
        with connect(DbRole.APP, dsn=streams["dsn"]["app"]) as conn, open_scoped_session(conn, principal) as s:
            yield s
    return _session
