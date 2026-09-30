"""Tests for schema/apply_migrations.py: exactly-once, ordered, immutable migrations."""
import psycopg
import pytest

from nacre.core.db import DbRole, connect
from nacre.schema.apply_migrations import SQL_DIR, MigrationError, apply_migrations


def _applied(dsn):
    with psycopg.connect(dsn) as c:
        return [r[0] for r in c.execute("SELECT name FROM public.nacre_schema_migrations ORDER BY version")]


def test_applies_all_shipped_migrations_in_order_then_nothing(fresh_db):
    with connect(DbRole.MIGRATOR, dsn=fresh_db) as conn:
        first = apply_migrations(conn)
        second = apply_migrations(conn)
    assert first == ["0001_ledger.sql", "0002_scopes_rls.sql", "0003_keys.sql", "0004_checkpoints.sql",
                     "0005_envelope_v2_trust_basis.sql", "0006_scope_org_integrity.sql",
                     "0007_keyadmin_role.sql",
                     "0008_orphan_collection.sql"]
    assert second == []
    assert _applied(fresh_db) == first


def test_all_objects_are_owned_by_nacre_migrator(fresh_db):
    with connect(DbRole.MIGRATOR, dsn=fresh_db) as conn:
        apply_migrations(conn)
        owners = conn.execute("""
            SELECT DISTINCT pg_get_userbyid(c.relowner) FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname IN ('ledger', 'scopes', 'keys')
            UNION
            SELECT DISTINCT pg_get_userbyid(p.proowner) FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname IN ('ledger', 'scopes', 'keys')
            UNION
            SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname IN ('ledger', 'scopes', 'keys')
        """).fetchall()
    assert owners == [("nacre_migrator",)]


def _tmp_migrations(tmp_path, files):
    d = tmp_path / "sql"
    d.mkdir()
    for name, text in files.items():
        (d / name).write_text(text)
    return d


def test_changed_applied_migration_is_refused(fresh_db, tmp_path):
    d = _tmp_migrations(tmp_path, {"0001_a.sql": "CREATE TABLE a (x int);"})
    with connect(DbRole.MIGRATOR, dsn=fresh_db) as conn:
        apply_migrations(conn, d)
        (d / "0001_a.sql").write_text("CREATE TABLE a (x bigint);")
        with pytest.raises(MigrationError, match="changed on disk"):
            apply_migrations(conn, d)


def test_missing_applied_migration_is_refused(fresh_db, tmp_path):
    d = _tmp_migrations(tmp_path, {"0001_a.sql": "CREATE TABLE a (x int);"})
    with connect(DbRole.MIGRATOR, dsn=fresh_db) as conn:
        apply_migrations(conn, d)
        (d / "0001_a.sql").unlink()
        with pytest.raises(MigrationError, match="missing from disk"):
            apply_migrations(conn, d)


def test_new_migration_numbered_below_an_applied_one_is_refused(fresh_db, tmp_path):
    d = _tmp_migrations(tmp_path, {"0002_b.sql": "CREATE TABLE b (x int);"})
    with connect(DbRole.MIGRATOR, dsn=fresh_db) as conn:
        apply_migrations(conn, d)
        (d / "0001_a.sql").write_text("CREATE TABLE a (x int);")
        with pytest.raises(MigrationError, match="numbered below"):
            apply_migrations(conn, d)


@pytest.mark.parametrize("bad", ["1_a.sql", "0001-a.sql", "0001_A.sql"])
def test_misnamed_migration_is_refused(tmp_path, bad):
    d = _tmp_migrations(tmp_path, {bad: "SELECT 1;"})
    with pytest.raises(MigrationError, match="names must look like"):
        apply_migrations(None, d)


def test_failed_migration_rolls_back_whole_file_and_is_not_recorded(fresh_db, tmp_path):
    d = _tmp_migrations(tmp_path, {
        "0001_a.sql": "CREATE TABLE a (x int);",
        "0002_b.sql": "CREATE TABLE b (x int); THIS IS NOT SQL;",
    })
    with connect(DbRole.MIGRATOR, dsn=fresh_db) as conn:
        with pytest.raises(psycopg.errors.SyntaxError):
            apply_migrations(conn, d)
    assert _applied(fresh_db) == ["0001_a.sql"]
    with psycopg.connect(fresh_db) as c:
        assert c.execute("SELECT to_regclass('a'), to_regclass('b')").fetchone() == ("a", None)


def test_shipped_migrations_directory_is_the_package_sql_dir():
    assert SQL_DIR.name == "sql" and (SQL_DIR / "0001_ledger.sql").exists()
