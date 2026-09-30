"""Tests for keys/get_or_create_key.py (D-0004): hierarchy, idempotence, isolation, shredding, tampering."""
import threading
import uuid
from datetime import date

import psycopg
import pytest

from nacre.keys.get_or_create_key import DataKey, KeyResolutionError, get_or_create_key, load_key

SEPT, OCT = date(2026, 9, 1), date(2026, 10, 1)


def _writer(session, streams):
    p = uuid.uuid4()
    return session(p, read=[streams["a"]], write=[streams["a"]])


def test_creates_master_and_data_key_then_returns_the_same_key(session, streams, provider):
    a = streams["a"]
    with _writer(session, streams) as s:
        k1 = get_or_create_key(s.conn, provider, a, a, SEPT)
        k2 = get_or_create_key(s.conn, provider, a, a, SEPT)
    assert isinstance(k1, DataKey) and len(k1.material) == 32
    assert (k1.key_id, k1.material) == (k2.key_id, k2.material)


def test_key_material_never_appears_in_repr(session, streams, provider):
    with _writer(session, streams) as s:
        k = get_or_create_key(s.conn, provider, streams["a"], streams["a"], SEPT)
    assert k.material.hex() not in repr(k) and "material" not in repr(k)


def test_distinct_keys_per_subject_and_month(session, streams, provider):
    a, person = streams["a"], uuid.uuid4()
    with _writer(session, streams) as s:
        system_sept = get_or_create_key(s.conn, provider, a, a, SEPT)
        system_oct = get_or_create_key(s.conn, provider, a, a, OCT)
        person_sept = get_or_create_key(s.conn, provider, a, person, SEPT)
    ids = {system_sept.key_id, system_oct.key_id, person_sept.key_id}
    materials = {system_sept.material, system_oct.material, person_sept.material}
    assert len(ids) == 3 and len(materials) == 3


def test_stored_keys_are_wrapped_not_plain(session, streams, provider):
    with _writer(session, streams) as s:
        k = get_or_create_key(s.conn, provider, streams["a"], streams["a"], SEPT)
    with psycopg.connect(streams["dsn"]["admin"]) as admin:
        (dek_wrapped,) = admin.execute("SELECT wrapped_key FROM keys.data_keys WHERE key_id = %s", (k.key_id,)).fetchone()
        (mk_wrapped,) = admin.execute("SELECT wrapped_key FROM keys.stream_master_keys WHERE stream_id = %s",
                                      (streams["a"],)).fetchone()
    assert k.material not in bytes(dek_wrapped) and k.material not in bytes(mk_wrapped)


def test_load_key_by_id(session, streams, provider):
    a = streams["a"]
    with _writer(session, streams) as s:
        k = get_or_create_key(s.conn, provider, a, a, SEPT)
    with session(uuid.uuid4(), read=[a]) as s:
        assert load_key(s.conn, provider, k.key_id) == k


def test_month_must_be_first_of_month(session, streams, provider):
    with _writer(session, streams) as s, pytest.raises(ValueError, match="first day"):
        get_or_create_key(s.conn, provider, streams["a"], streams["a"], date(2026, 9, 15))


def test_read_only_principal_cannot_create_keys(session, streams, provider):
    with session(uuid.uuid4(), read=[streams["a"]]) as s:
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security"):
            get_or_create_key(s.conn, provider, streams["a"], streams["a"], SEPT)


def test_other_streams_keys_are_invisible(session, streams, provider):
    a, b = streams["a"], streams["b"]
    with session(uuid.uuid4(), read=[b], write=[b]) as s:
        kb = get_or_create_key(s.conn, provider, b, b, SEPT)
    with _writer(session, streams) as s:
        assert load_key(s.conn, provider, kb.key_id) is None


def test_shredded_data_key_loads_as_none(session, streams, provider):
    a = streams["a"]
    with _writer(session, streams) as s:
        k = get_or_create_key(s.conn, provider, a, a, SEPT)
    with psycopg.connect(streams["dsn"]["admin"]) as admin:
        admin.execute("DELETE FROM keys.data_keys WHERE key_id = %s", (k.key_id,))
    with session(uuid.uuid4(), read=[a]) as s:
        assert load_key(s.conn, provider, k.key_id) is None


def test_shredded_master_key_makes_its_data_keys_none(session, streams, provider):
    a = streams["a"]
    with _writer(session, streams) as s:
        k = get_or_create_key(s.conn, provider, a, a, SEPT)
    with psycopg.connect(streams["dsn"]["admin"]) as admin:
        admin.execute("DELETE FROM keys.stream_master_keys WHERE stream_id = %s", (a,))
    with session(uuid.uuid4(), read=[a]) as s:
        assert load_key(s.conn, provider, k.key_id) is None


def test_wrapped_data_key_moved_to_another_row_fails(session, streams, provider):
    a, person = streams["a"], uuid.uuid4()
    with _writer(session, streams) as s:
        k1 = get_or_create_key(s.conn, provider, a, a, SEPT)
        k2 = get_or_create_key(s.conn, provider, a, person, SEPT)
    with psycopg.connect(streams["dsn"]["admin"]) as admin:
        admin.execute("UPDATE keys.data_keys SET wrapped_key = (SELECT wrapped_key FROM keys.data_keys WHERE key_id = %s) "
                      "WHERE key_id = %s", (k1.key_id, k2.key_id))
    with session(uuid.uuid4(), read=[a]) as s, pytest.raises(KeyResolutionError, match="authentication"):
        load_key(s.conn, provider, k2.key_id)


def test_master_key_moved_to_another_stream_fails(session, streams, provider):
    a, b = streams["a"], streams["b"]
    with session(uuid.uuid4(), read=[a, b], write=[a, b]) as s:
        get_or_create_key(s.conn, provider, a, a, SEPT)
        kb = get_or_create_key(s.conn, provider, b, b, SEPT)
    with psycopg.connect(streams["dsn"]["admin"]) as admin:
        admin.execute("UPDATE keys.stream_master_keys SET wrapped_key = (SELECT wrapped_key FROM keys.stream_master_keys "
                      "WHERE stream_id = %s) WHERE stream_id = %s", (a, b))
    with session(uuid.uuid4(), read=[b]) as s, pytest.raises(KeyResolutionError, match="cannot be unwrapped"):
        load_key(s.conn, provider, kb.key_id)


def test_destroyed_root_version_makes_keys_unresolvable(session, streams, provider):
    a = streams["a"]
    with _writer(session, streams) as s:
        k = get_or_create_key(s.conn, provider, a, a, SEPT)
    old = provider.current_version()
    provider.create_version()
    provider.destroy_version(old)
    with session(uuid.uuid4(), read=[a]) as s, pytest.raises(KeyResolutionError, match="not held"):
        load_key(s.conn, provider, k.key_id)


def test_concurrent_creators_get_the_same_key(session, streams, provider):
    a = streams["a"]
    barrier, results, errors = threading.Barrier(4), [], []

    def worker():
        try:
            with session(uuid.uuid4(), read=[a], write=[a]) as s:
                barrier.wait()
                results.append(get_or_create_key(s.conn, provider, a, a, SEPT))
        except Exception as exc:  # noqa: BLE001 - surfaced by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len({(k.key_id, k.material) for k in results}) == 1
    with psycopg.connect(streams["dsn"]["admin"]) as admin:
        assert admin.execute("SELECT count(*) FROM keys.data_keys").fetchone()[0] == 1
        assert admin.execute("SELECT count(*) FROM keys.stream_master_keys").fetchone()[0] == 1
