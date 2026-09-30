"""Tests for migration 0008 (D-0015): nacre_gc reads only attachment references; the attachment lock functions."""
import uuid

import psycopg
import pytest


@pytest.fixture
def streams_dsn(streams):
    return streams["dsn"]


@pytest.mark.parametrize("sql", [
    "SELECT body_ciphertext FROM ledger.events",
    "SELECT stream_id FROM ledger.events",
    "SELECT 1 FROM keys.data_keys",
    "SELECT 1 FROM keys.stream_master_keys",
    "SELECT 1 FROM scopes.scopes",
    "SELECT 1 FROM ledger.checkpoints",
    "INSERT INTO ledger.events (attachment_ref) VALUES (NULL)",
])
def test_the_gc_role_reads_only_attachment_references(streams_dsn, sql):
    with psycopg.connect(streams_dsn["gc"]) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute(sql)


def test_the_gc_role_sees_references_in_every_stream(streams_dsn):
    with psycopg.connect(streams_dsn["gc"]) as c:
        assert c.execute("SELECT count(attachment_ref) FROM ledger.events").fetchone()[0] == 0   # allowed, all rows


def test_the_attachment_lock_is_callable_by_app_and_gc_only(streams_dsn):
    ref = uuid.uuid4().bytes * 2
    for role in ("app", "gc"):
        with psycopg.connect(streams_dsn[role]) as c:
            assert c.execute("SELECT pg_try_advisory_xact_lock(ledger.attachment_lock_namespace(), "
                             "ledger.attachment_lock_key(%s))", (ref,)).fetchone()[0]
    with psycopg.connect(streams_dsn["verifier"]) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("SELECT ledger.attachment_lock_key(%s)", (ref,))


def test_attachment_locks_do_not_block_stream_locks(streams_dsn, streams):
    # Two-integer advisory keys are a separate space from the one-bigint stream lock (D-0015).
    ref = uuid.uuid4().bytes * 2
    with psycopg.connect(streams_dsn["gc"]) as gc, psycopg.connect(streams_dsn["app"]) as app:
        gc.execute("SELECT pg_advisory_lock(ledger.attachment_lock_namespace(), ledger.attachment_lock_key(%s))", (ref,))
        assert app.execute("SELECT pg_try_advisory_xact_lock(ledger.stream_lock_key(%s))",
                           (streams["a"],)).fetchone()[0]
