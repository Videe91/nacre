"""Tests for scopes/bootstrap_org.py (D-0005): the org's first event, its registry row, the owner's grant."""
import uuid

import psycopg
import pytest

from nacre.core.db import DbRole, connect
from nacre.keys.decrypt_payload import decrypt_payload
from nacre.scopes.bootstrap_org import BootstrapError, bootstrap_org


def test_bootstrap_records_first_event_scope_and_owner_grant(org, provider, migrated_db):
    org_id, owner, open_ = org
    with open_(owner) as s:
        assert s.access.read_streams == {org_id} and s.access.write_streams == {org_id}
        (seq, event_id, key_id, etype, ptype) = s.conn.execute(
            "SELECT commit_seq, event_id, key_id, event_type, payload_type FROM ledger.events WHERE stream_id = %s",
            (org_id,)).fetchone()
        aad = {"envelope_version": 2, "event_id": event_id, "stream_id": org_id, "key_id": key_id,
               "event_type": etype, "payload_type": ptype}
        ct = bytes(s.conn.execute("SELECT body_ciphertext FROM ledger.events WHERE event_id = %s", (event_id,)).fetchone()[0])
        body = decrypt_payload(s.conn, provider, aad, ct)
    assert seq == 1 and body["content"] == {"op": "bootstrap_org", "org_id": str(org_id), "owner": str(owner)}
    with psycopg.connect(migrated_db["admin"]) as c:
        assert c.execute("SELECT kind, source_event_id FROM scopes.scopes WHERE stream_id = %s", (org_id,)).fetchone() \
            == ("org", event_id)
        assert c.execute("SELECT source_event_id, source_seq FROM scopes.scope_grants WHERE principal_id = %s",
                         (owner,)).fetchone() == (event_id, 1)


def test_bootstrap_refuses_a_non_admin_connection(migrated_db, provider):
    with connect(DbRole.APP, dsn=migrated_db["app"]) as conn, pytest.raises(BootstrapError, match="admin"):
        bootstrap_org(conn, provider, owner_principal_id=uuid.uuid4(), idempotency_key=str(uuid.uuid4()))


def test_bootstrap_is_atomic(migrated_db, provider):
    with connect(DbRole.MIGRATOR, dsn=migrated_db["admin"]) as admin, pytest.raises(Exception):
        bootstrap_org(admin, provider, owner_principal_id=uuid.uuid4(), idempotency_key="not-a-uuid")
    with psycopg.connect(migrated_db["admin"]) as c:
        assert c.execute("SELECT count(*) FROM ledger.events").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM scopes.scopes").fetchone()[0] == 0
