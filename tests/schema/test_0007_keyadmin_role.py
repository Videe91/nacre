"""Tests for schema/sql/0007_keyadmin_role.sql (D-0014): least privilege for key administration."""
import psycopg
import pytest

DENIED = psycopg.errors.InsufficientPrivilege


def test_keyadmin_owns_nothing_cannot_bypass_rls_and_does_not_inherit_app(migrated_db):
    with psycopg.connect(migrated_db["admin"]) as c:
        flags = c.execute("SELECT rolsuper, rolbypassrls, rolinherit FROM pg_roles WHERE rolname = 'nacre_keyadmin'").fetchone()
        owned = c.execute("""SELECT count(*) FROM (SELECT relowner AS o FROM pg_class UNION ALL SELECT proowner FROM pg_proc
                             UNION ALL SELECT nspowner FROM pg_namespace) x
                             WHERE o = (SELECT oid FROM pg_roles WHERE rolname = 'nacre_keyadmin')""").fetchone()[0]
    assert flags == (False, False, False) and owned == 0


@pytest.mark.parametrize("table", ["ledger.events", "ledger.checkpoints"])
def test_keyadmin_never_reads_events_or_checkpoints(migrated_db, table):
    with psycopg.connect(migrated_db["keyadmin"]) as c, pytest.raises(DENIED):
        c.execute(f"SELECT 1 FROM {table}")


@pytest.mark.parametrize("role,table,privilege,expected", [
    ("nacre_keyadmin", "keys.stream_master_keys", "SELECT", True),
    ("nacre_keyadmin", "keys.stream_master_keys", "DELETE", True),
    ("nacre_keyadmin", "keys.stream_master_keys", "INSERT", False),
    ("nacre_keyadmin", "keys.data_keys", "DELETE", True),
    ("nacre_keyadmin", "keys.data_keys", "INSERT", False),
    ("nacre_keyadmin", "scopes.scopes", "SELECT", True),
    ("nacre_keyadmin", "scopes.scopes", "INSERT", False),
    ("nacre_keyadmin", "scopes.scope_grants", "INSERT", False),
    ("nacre_keyadmin", "ledger.events", "SELECT", False),
    ("nacre_app", "keys.data_keys", "DELETE", False),                  # the app still cannot destroy keys
])
def test_table_privileges(migrated_db, role, table, privilege, expected):
    with psycopg.connect(migrated_db["admin"]) as c:
        assert c.execute("SELECT has_table_privilege(%s, %s, %s)", (role, table, privilege)).fetchone()[0] is expected


def test_keyadmin_can_act_as_app_only_by_explicit_set_role(migrated_db):
    with psycopg.connect(migrated_db["keyadmin"]) as c:
        with pytest.raises(DENIED):
            c.execute("INSERT INTO ledger.events (event_id) VALUES (gen_random_uuid())")   # not inherited
        c.rollback()
        c.execute("SET LOCAL ROLE nacre_app")
        assert c.execute("SELECT current_user").fetchone()[0] == "nacre_app"
