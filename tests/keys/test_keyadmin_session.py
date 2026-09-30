"""Tests for keys/keyadmin_session.py: a pooled key-admin connection returns to its base role with no settings left,
after a clean commit and after an error mid-transaction (owner, approval of the NOINHERIT choice)."""
import uuid

import psycopg
import pytest

from nacre.core.db import DbRole, open_pool
from nacre.keys.keyadmin_session import KeyAdminError, keyadmin_transaction

SETTINGS = ("nacre.principal", "nacre.read_streams", "nacre.write_streams")


def _state(conn):
    row = conn.execute("SELECT current_user, pg_backend_pid(), " +
                       ", ".join(f"coalesce(current_setting('{s}', true), '')" for s in SETTINGS)).fetchone()
    conn.rollback()
    return row[0], row[1], row[2:]


def _assert_base(conn):
    user, _, settings = _state(conn)
    assert user == "nacre_keyadmin" and settings == ("", "", "")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):                  # NOINHERIT: no app rights at base
        conn.execute("SELECT 1 FROM ledger.events")
    conn.rollback()


@pytest.fixture
def pool(migrated_db):
    p = open_pool(DbRole.KEYADMIN, migrated_db["keyadmin"], min_size=1, max_size=1)   # one physical connection
    yield p
    p.close()


def test_a_pooled_connection_returns_to_its_base_role_after_a_clean_transaction(pool):
    s = uuid.uuid4()
    with pool.connection() as c:
        pid = _state(c)[1]
        with keyadmin_transaction(c) as tx:
            tx.as_app(uuid.uuid4(), {s}, {s})
            assert c.execute("SELECT current_user").fetchone()[0] == "nacre_app"
    with pool.connection() as c:
        assert _state(c)[1] == pid                                               # the same physical connection
        _assert_base(c)


@pytest.mark.parametrize("fail_in", ["app", "keyadmin"])
def test_a_pooled_connection_returns_to_its_base_role_after_an_error(pool, fail_in):
    s = uuid.uuid4()
    with pool.connection() as c:
        pid = _state(c)[1]
        with pytest.raises(RuntimeError, match="mid-transaction"):
            with keyadmin_transaction(c) as tx:
                tx.as_app(uuid.uuid4(), {s}, {s})
                if fail_in == "keyadmin":
                    tx.as_keyadmin()
                raise RuntimeError("mid-transaction failure")
    with pool.connection() as c:
        assert _state(c)[1] == pid
        _assert_base(c)


def test_a_database_error_mid_transaction_also_resets(pool):
    with pool.connection() as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with keyadmin_transaction(c) as tx:
                tx.as_app(uuid.uuid4(), set(), set())
                c.execute("SELECT 1 FROM ledger.checkpoints")                    # nacre_app may not read these
    with pool.connection() as c:
        _assert_base(c)


def test_it_refuses_a_non_keyadmin_login_and_an_open_transaction(migrated_db):
    with psycopg.connect(migrated_db["app"]) as c:
        with pytest.raises(KeyAdminError, match="nacre_keyadmin login"):
            with keyadmin_transaction(c):
                pass
    with psycopg.connect(migrated_db["keyadmin"]) as c:
        c.execute("SELECT 1")
        with pytest.raises(KeyAdminError, match="outside a transaction"):
            with keyadmin_transaction(c):
                pass
