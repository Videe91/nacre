"""Tests for schema/sql/0001_ledger.sql: append-only, seal linkage, envelope checks, role ownership."""
import uuid
from datetime import UTC, datetime

import psycopg
import pytest

APPEND_ONLY = psycopg.errors.InsufficientPrivilege


def _admin(db):
    return psycopg.connect(db["admin"])


# --- append-only (D-0003) -------------------------------------------------------------------

@pytest.mark.parametrize("stmt", [
    "UPDATE ledger.events SET body_ciphertext = 'x'",
    "DELETE FROM ledger.events",
    "TRUNCATE ledger.events",
])
def test_even_a_superuser_cannot_mutate_events(migrated_db, helpers, stmt):
    with _admin(migrated_db) as conn:
        helpers.insert_chain(conn, uuid.uuid4(), 2)
        conn.commit()
        with pytest.raises(APPEND_ONLY, match="append-only"):
            conn.execute(stmt)


def test_owner_cannot_truncate_and_its_updates_touch_nothing(migrated_db, helpers):
    with _admin(migrated_db) as conn:
        rows = helpers.insert_chain(conn, uuid.uuid4(), 1)
        conn.commit()
        conn.execute("SET ROLE nacre_migrator")
        # FORCE RLS with no owner policy: the owner sees no rows, so UPDATE/DELETE change nothing.
        assert conn.execute("UPDATE ledger.events SET body_ciphertext = 'x'").rowcount == 0
        assert conn.execute("DELETE FROM ledger.events").rowcount == 0
        with pytest.raises(APPEND_ONLY, match="append-only"):
            conn.execute("TRUNCATE ledger.events")
        conn.rollback()
        conn.execute("RESET ROLE")
        body = conn.execute("SELECT body_ciphertext FROM ledger.events WHERE event_id = %s",
                            (rows[0]["event_id"],)).fetchone()[0]
        assert bytes(body) == b"ciphertext"


@pytest.mark.parametrize("stmt", [
    "UPDATE ledger.events SET body_ciphertext = 'x'",
    "DELETE FROM ledger.events",
    "TRUNCATE ledger.events",
])
def test_app_role_has_no_mutation_privilege(migrated_db, stmt):
    with psycopg.connect(migrated_db["app"]) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="permission denied"):
            conn.execute(stmt)


# --- seal linkage (D-0003) ------------------------------------------------------------------

def test_valid_chain_is_accepted(migrated_db, helpers):
    with _admin(migrated_db) as conn:
        helpers.insert_chain(conn, uuid.uuid4(), 5)


def test_first_event_must_be_seq_1_with_zero_prev_hash(migrated_db, helpers):
    s = uuid.uuid4()
    with _admin(migrated_db) as conn:
        with pytest.raises(psycopg.errors.CheckViolation, match="seal linkage"):
            helpers.insert_event(conn, helpers.event_row(s, 2, helpers.ZERO_HASH))
        conn.rollback()
        with pytest.raises(psycopg.errors.CheckViolation, match="seal linkage"):
            helpers.insert_event(conn, helpers.event_row(s, 1, b"\x01" * 32))


def test_sequence_gap_is_rejected(migrated_db, helpers):
    s = uuid.uuid4()
    with _admin(migrated_db) as conn:
        rows = helpers.insert_chain(conn, s, 2)
        with pytest.raises(psycopg.errors.CheckViolation, match="expects commit_seq 3"):
            helpers.insert_event(conn, helpers.event_row(s, 4, rows[-1]["hash"]))


def test_wrong_prev_hash_is_rejected(migrated_db, helpers):
    s = uuid.uuid4()
    with _admin(migrated_db) as conn:
        helpers.insert_chain(conn, s, 2)
        with pytest.raises(psycopg.errors.CheckViolation, match="seal linkage"):
            helpers.insert_event(conn, helpers.event_row(s, 3, b"\x02" * 32))


def test_streams_chain_independently(migrated_db, helpers):
    with _admin(migrated_db) as conn:
        helpers.insert_chain(conn, uuid.uuid4(), 3)
        helpers.insert_chain(conn, uuid.uuid4(), 3)


def test_duplicate_idempotency_key_in_a_stream_is_rejected(migrated_db, helpers):
    s = uuid.uuid4()
    with _admin(migrated_db) as conn:
        rows = helpers.insert_chain(conn, s, 1)
        with pytest.raises(psycopg.errors.UniqueViolation):
            helpers.insert_event(conn, helpers.event_row(
                s, 2, rows[0]["hash"], idempotency_key=rows[0]["idempotency_key"]))


def test_caused_by_must_be_in_the_same_stream(migrated_db, helpers):
    a, b = uuid.uuid4(), uuid.uuid4()
    with _admin(migrated_db) as conn:
        in_a = helpers.insert_chain(conn, a, 1)
        in_b = helpers.insert_chain(conn, b, 1)
        helpers.insert_event(conn, helpers.event_row(a, 2, in_a[0]["hash"], caused_by=in_a[0]["event_id"]))
        with pytest.raises(psycopg.errors.ForeignKeyViolation, match="caused_by"):
            helpers.insert_event(conn, helpers.event_row(b, 2, in_b[0]["hash"], caused_by=in_a[0]["event_id"]))


# --- envelope checks (D-0002, MNEXA ADR-0010) -------------------------------------------------

def test_envelope_constraints_base_row_is_valid(migrated_db, helpers):
    with _admin(migrated_db) as conn:
        helpers.insert_event(conn, helpers.event_row(uuid.uuid4(), 1, helpers.ZERO_HASH,
                             occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
                             occurred_at_basis="asserted", occurred_at_precision="second"))


@pytest.mark.parametrize("overrides", [
    {"occurred_at_basis": "observed", "trust": "untrusted"},     # observed needs a trusted runtime
    {"occurred_at_basis": "inferred"},                             # no inferred basis exists
    {"occurred_at_precision": None},                               # basis and precision go together
    {"actor_model": "alice@example.com"},                          # identity can't hide in identifiers
    {"actor_tool": "rm -rf"},                                      # no spaces
    {"event_type": "correction"},                                  # correction needs caused_by
    {"envelope_version": 2},
    {"request_mac": b"short"},
], ids=lambda o: next(iter(o)))
def test_envelope_constraints_reject(migrated_db, helpers, overrides):
    row = helpers.event_row(uuid.uuid4(), 1, helpers.ZERO_HASH, occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
                            occurred_at_basis="asserted", occurred_at_precision="second")
    row.update(overrides)
    with _admin(migrated_db) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            helpers.insert_event(conn, row)


def test_late_world_time_is_admissible(migrated_db, helpers):
    # MNEXA ADR-0010 R-13: an event whose occurred_at precedes an earlier commit is accepted.
    s = uuid.uuid4()
    with _admin(migrated_db) as conn:
        first = helpers.insert_chain(conn, s, 1)[0]
        late = first["recorded_at"].replace(year=2020)
        helpers.insert_event(conn, helpers.event_row(
            s, 2, first["hash"], occurred_at=late, occurred_at_basis="asserted", occurred_at_precision="day"))


# --- D-0005 S-1: the app and verifier own nothing and cannot bypass RLS ----------------------

@pytest.mark.parametrize("role", ["nacre_app", "nacre_verifier"])
def test_s1_role_owns_nothing_and_cannot_bypass_rls(migrated_db, role):
    with _admin(migrated_db) as conn:
        flags = conn.execute("SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb FROM pg_roles WHERE rolname = %s",
                             (role,)).fetchone()
        assert flags == (False, False, False, False)
        owned = conn.execute("""
            SELECT count(*) FROM (
              SELECT relowner AS o FROM pg_class UNION ALL
              SELECT proowner FROM pg_proc UNION ALL
              SELECT nspowner FROM pg_namespace UNION ALL
              SELECT typowner FROM pg_type) x
            WHERE o = (SELECT oid FROM pg_roles WHERE rolname = %s)""", (role,)).fetchone()[0]
        assert owned == 0
