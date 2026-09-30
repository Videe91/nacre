"""Tests for schema/sql/0002_scopes_rls.sql: D-0005 S-2 (forced RLS) and the basic S-3 wall.
The full adversarial suite (every scope-kind pair, pooled reuse through the real door) lives with
scopes/open_scoped_session.py (INDEX #10)."""
import uuid

import psycopg
import pytest

RLS_TABLES = ["ledger.events", "scopes.scopes", "scopes.scope_grants"]


@pytest.mark.parametrize("table", RLS_TABLES)
def test_s2_rls_enabled_and_forced(migrated_db, table):
    with psycopg.connect(migrated_db["admin"]) as conn:
        flags = conn.execute("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = %s::regclass",
                             (table,)).fetchone()
    assert flags == (True, True)


@pytest.fixture
def two_streams(migrated_db, helpers):
    a, b = uuid.uuid4(), uuid.uuid4()
    with psycopg.connect(migrated_db["admin"]) as conn:
        helpers.insert_chain(conn, a, 3)
        helpers.insert_chain(conn, b, 2)
    return a, b


def _streams_seen(conn):
    return {r[0] for r in conn.execute("SELECT DISTINCT stream_id FROM ledger.events")}


def test_s3_app_reads_only_its_streams(migrated_db, helpers, two_streams):
    a, b = two_streams
    with psycopg.connect(migrated_db["app"]) as conn:
        helpers.scope_to(conn, read=[a])
        assert _streams_seen(conn) == {a}
        assert conn.execute("SELECT count(*) FROM ledger.events WHERE stream_id = %s", (b,)).fetchone()[0] == 0


def test_s3_no_setting_means_no_rows(migrated_db, two_streams):
    with psycopg.connect(migrated_db["app"]) as conn:
        assert _streams_seen(conn) == set()


def test_s3_settings_die_with_the_transaction_on_a_reused_connection(migrated_db, helpers, two_streams):
    a, _ = two_streams
    with psycopg.connect(migrated_db["app"]) as conn:
        helpers.scope_to(conn, read=[a])
        assert _streams_seen(conn) == {a}
        conn.commit()
        assert _streams_seen(conn) == set()   # next request on the same connection sees nothing
        conn.rollback()
        helpers.scope_to(conn, read=[a])
        conn.rollback()
        assert _streams_seen(conn) == set()


def test_s3_savepoint_rollback_restores_the_outer_scope(migrated_db, helpers, two_streams):
    a, b = two_streams
    with psycopg.connect(migrated_db["app"]) as conn:
        helpers.scope_to(conn, read=[a])
        conn.execute("SAVEPOINT sp")
        helpers.scope_to(conn, read=[b])
        assert _streams_seen(conn) == {b}
        conn.execute("ROLLBACK TO SAVEPOINT sp")
        assert _streams_seen(conn) == {a}


def test_s3_malformed_setting_fails_closed_with_an_error(migrated_db, two_streams):
    with psycopg.connect(migrated_db["app"]) as conn:
        conn.execute("SELECT set_config('nacre.read_streams', 'not-a-uuid-array', true)")
        with pytest.raises(psycopg.errors.InvalidTextRepresentation):
            _streams_seen(conn)


def test_s3_app_cannot_append_outside_its_write_set(migrated_db, helpers):
    a, b = uuid.uuid4(), uuid.uuid4()
    with psycopg.connect(migrated_db["app"]) as conn:
        helpers.scope_to(conn, read=[a, b], write=[a])
        helpers.insert_event(conn, helpers.event_row(a, 1, helpers.ZERO_HASH))
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security"):
            helpers.insert_event(conn, helpers.event_row(b, 1, helpers.ZERO_HASH))


def test_append_without_read_fails_closed(migrated_db, helpers, two_streams):
    a, _ = two_streams  # stream a already holds 3 events
    with psycopg.connect(migrated_db["app"]) as conn:
        helpers.scope_to(conn, read=[], write=[a])
        # The linkage trigger cannot see a's head, so a plausible next event is rejected.
        with pytest.raises(psycopg.errors.CheckViolation, match="seal linkage"):
            helpers.insert_event(conn, helpers.event_row(a, 4, helpers.ZERO_HASH))


def test_verifier_reads_every_stream_but_no_scopes_or_keys(migrated_db, two_streams):
    a, b = two_streams
    with psycopg.connect(migrated_db["verifier"]) as conn:
        assert _streams_seen(conn) == {a, b}
        for table in ("scopes.scopes", "scopes.scope_grants", "keys.stream_master_keys", "keys.data_keys"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(f"SELECT 1 FROM {table}")
            conn.rollback()


def test_principal_sees_only_its_own_grants(migrated_db, helpers):
    org, p1, p2 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with psycopg.connect(migrated_db["admin"]) as conn:
        conn.execute("INSERT INTO scopes.scopes (stream_id, kind, org_id, source_event_id) VALUES (%s, 'org', %s, %s)",
                     (org, org, uuid.uuid4()))
        for seq, p in enumerate((p1, p2), start=1):
            conn.execute("""INSERT INTO scopes.scope_grants
                            (principal_id, stream_id, org_id, can_read, can_append, source_event_id, source_seq)
                            VALUES (%s, %s, %s, true, false, %s, %s)""", (p, org, org, uuid.uuid4(), seq))
    with psycopg.connect(migrated_db["app"]) as conn:
        helpers.scope_to(conn, principal=p1)
        assert [r[0] for r in conn.execute("SELECT principal_id FROM scopes.scope_grants")] == [p1]


def test_grant_rows_are_append_only(migrated_db):
    with psycopg.connect(migrated_db["admin"]) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="append-only"):
            conn.execute("TRUNCATE scopes.scope_grants")


def test_append_implies_read_in_grants(migrated_db):
    org = uuid.uuid4()
    with psycopg.connect(migrated_db["admin"]) as conn:
        conn.execute("INSERT INTO scopes.scopes (stream_id, kind, org_id, source_event_id) VALUES (%s, 'org', %s, %s)",
                     (org, org, uuid.uuid4()))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("""INSERT INTO scopes.scope_grants
                            (principal_id, stream_id, org_id, can_read, can_append, source_event_id, source_seq)
                            VALUES (%s, %s, %s, false, true, %s, 1)""", (uuid.uuid4(), org, org, uuid.uuid4()))



# ---- 0006: org integrity ----------------------------------------------------------------------------------
def test_grant_must_name_a_stream_of_the_same_org(migrated_db):
    org_a, org_b, proj_b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with psycopg.connect(migrated_db["admin"]) as conn:
        for s, kind, org in ((org_a, "org", org_a), (org_b, "org", org_b), (proj_b, "project", org_b)):
            conn.execute("INSERT INTO scopes.scopes (stream_id, kind, org_id, source_event_id) VALUES (%s, %s, %s, %s)",
                         (s, kind, org, uuid.uuid4()))
        conn.commit()
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            conn.execute("""INSERT INTO scopes.scope_grants (principal_id, stream_id, org_id, can_read, can_append,
                            source_event_id, source_seq) VALUES (%s, %s, %s, true, false, %s, 1)""",
                         (uuid.uuid4(), proj_b, org_a, uuid.uuid4()))
        conn.rollback()
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            conn.execute("INSERT INTO scopes.scopes (stream_id, kind, org_id, parent_stream_id, source_event_id) "
                         "VALUES (%s, 'team', %s, %s, %s)", (uuid.uuid4(), org_a, proj_b, uuid.uuid4()))
