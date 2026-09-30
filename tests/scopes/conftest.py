"""Fixtures for scope tests: a world of streams of every kind, with events and keys, and grants."""
import uuid
from datetime import date

import psycopg
import pytest

from nacre.ledger.seal_event import GENESIS_PREV_HASH

KINDS = ["org", "team", "project", "user", "agent"]
_seq = iter(range(1, 10**6))


def add_grant(admin, principal, stream, org, read, append):
    admin.execute("""INSERT INTO scopes.scope_grants
                     (principal_id, stream_id, org_id, can_read, can_append, source_event_id, source_seq)
                     VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                  (principal, stream, org, read, append, uuid.uuid4(), next(_seq)))


def _add_event(admin, stream, seq, prev):
    h = uuid.uuid4().bytes * 2
    admin.execute("""INSERT INTO ledger.events (envelope_version, event_id, stream_id, commit_seq, recorded_at,
                       committed_at, event_type, payload_type, actor_kind, actor_id, source, trust, key_id,
                       idempotency_key, request_mac, body_ciphertext, prev_hash, hash)
                     VALUES (1, %s, %s, %s, now(), now(), 'message', 'text', 'agent', %s, 'chat', 'untrusted',
                             %s, %s, %s, 'c', %s, %s)""",
                  (uuid.uuid7(), stream, seq, uuid.uuid4(), uuid.uuid4(), f"k{seq}", bytes(32), prev, h))
    return h


@pytest.fixture
def world(migrated_db):
    """Two streams of every kind (…['project'][0], …['project'][1]) in one org, each holding 2 events,
    a master key and a data key. Returns {'org': id, 'streams': {kind: [s0, s1]}, 'dsn': migrated_db}."""
    org = uuid.uuid4()
    streams = {k: ([org, uuid.uuid4()] if k == "org" else [uuid.uuid4(), uuid.uuid4()]) for k in KINDS}
    with psycopg.connect(migrated_db["admin"]) as admin:
        for kind, ids in streams.items():
            for s in ids:
                org_of = s if kind == "org" else org
                admin.execute("INSERT INTO scopes.scopes (stream_id, kind, org_id, source_event_id) VALUES (%s, %s, %s, %s)",
                              (s, kind, org_of, uuid.uuid4()))
                prev = _add_event(admin, s, 1, GENESIS_PREV_HASH)
                _add_event(admin, s, 2, prev)
                admin.execute("INSERT INTO keys.stream_master_keys (stream_id, root_key_version, wrapped_key) "
                              "VALUES (%s, 'v1', 'w')", (s,))
                admin.execute("INSERT INTO keys.data_keys (key_id, stream_id, subject_id, month, wrapped_key) "
                              "VALUES (%s, %s, %s, %s, 'w')", (uuid.uuid4(), s, s, date(2026, 9, 1)))
    return {"org": org, "streams": streams, "dsn": migrated_db}


@pytest.fixture
def grant(world):
    def _grant(principal, stream, read=True, append=False):
        org = stream if stream in world["streams"]["org"] else world["org"]
        with psycopg.connect(world["dsn"]["admin"]) as admin:
            add_grant(admin, principal, stream, org, read, append)
    return _grant

