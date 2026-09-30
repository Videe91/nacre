"""Tests for keys/decrypt_payload.py (D-0004, D-0008): round trip, tamper rejection, Shredded, readability."""
import uuid
from datetime import date

import psycopg
import pytest

from nacre.core.event import EventType, PayloadType, new_event_id
from nacre.keys.decrypt_payload import DecryptError, Shredded, decrypt_payload
from nacre.keys.encrypt_payload import encrypt_payload
from nacre.keys.get_or_create_key import get_or_create_key

SEPT = date(2026, 9, 1)
BODY = {"content_version": 1, "content": {"tests": 3, "passed": True}, "person": {"name": "Ada"},
        "redactions": ["github-pat"]}


@pytest.fixture
def sealed(session, streams, provider):
    """An encrypted body and its AAD fields, committed (so later sessions can read it)."""
    a = streams["a"]
    with session(uuid.uuid4(), read=[a], write=[a]) as s:
        key = get_or_create_key(s.conn, provider, a, uuid.uuid4(), SEPT)
        fields = {"envelope_version": 1, "event_id": new_event_id(), "stream_id": a, "key_id": key.key_id,
                  "event_type": EventType.STATEMENT, "payload_type": PayloadType.STRUCTURED}
        ct = encrypt_payload(s.conn, key, fields, BODY)
    return fields, ct, key


def _read(session, streams, provider, fields, ct, stream=None):
    with session(uuid.uuid4(), read=[stream or streams["a"]]) as s:
        return decrypt_payload(s.conn, provider, fields, ct)


def test_round_trip(sealed, session, streams, provider):
    fields, ct, _ = sealed
    assert _read(session, streams, provider, fields, ct) == BODY


@pytest.mark.parametrize("field,value", [
    ("event_id", uuid.uuid4()), ("event_type", EventType.MESSAGE), ("payload_type", PayloadType.TEXT),
])
def test_changing_any_aad_field_fails_authentication(sealed, session, streams, provider, field, value):
    fields, ct, _ = sealed
    with pytest.raises(DecryptError, match="authentication failed"):
        _read(session, streams, provider, {**fields, field: value}, ct)


@pytest.mark.parametrize("offset", [31, 40, -1])
def test_flipped_ciphertext_or_tag_byte_fails(sealed, session, streams, provider, offset):
    fields, ct, _ = sealed
    b = bytearray(ct)
    b[offset] ^= 0x01
    with pytest.raises(DecryptError, match="authentication failed"):
        _read(session, streams, provider, fields, bytes(b))


def test_nonce_change_fails(sealed, session, streams, provider):
    fields, ct, _ = sealed
    b = bytearray(ct)
    b[20] ^= 0x01
    with pytest.raises(DecryptError, match="authentication failed"):
        _read(session, streams, provider, fields, bytes(b))


@pytest.mark.parametrize("byte,value,match", [(0, 2, "unknown format"), (1, 2, "unknown format"),
                                             (2, 1, "flags"), (2, 0x80, "flags")])
def test_header_is_checked_before_anything_else(sealed, session, streams, provider, byte, value, match):
    fields, ct, _ = sealed
    b = bytearray(ct)
    b[byte] = value
    with pytest.raises(DecryptError, match=match):
        _read(session, streams, provider, fields, bytes(b))


def test_header_key_id_must_match_envelope(sealed, session, streams, provider):
    fields, ct, _ = sealed
    with pytest.raises(DecryptError, match="key_id"):
        _read(session, streams, provider, {**fields, "key_id": uuid.uuid4()}, ct)


def test_truncated_ciphertext(sealed, session, streams, provider):
    fields, ct, _ = sealed
    with pytest.raises(DecryptError, match="shorter"):
        _read(session, streams, provider, fields, ct[:40])


def test_shredded_data_key_reads_as_shredded(sealed, session, streams, provider):
    fields, ct, key = sealed
    with psycopg.connect(streams["dsn"]["admin"]) as admin:
        admin.execute("DELETE FROM keys.data_keys WHERE key_id = %s", (key.key_id,))
    assert _read(session, streams, provider, fields, ct) == Shredded(key.key_id)


def test_shredded_stream_reads_as_shredded(sealed, session, streams, provider):
    fields, ct, key = sealed
    with psycopg.connect(streams["dsn"]["admin"]) as admin:
        admin.execute("DELETE FROM keys.stream_master_keys WHERE stream_id = %s", (streams["a"],))
    assert _read(session, streams, provider, fields, ct) == Shredded(key.key_id)


def test_unreadable_stream_is_refused_not_reported_shredded(sealed, session, streams, provider):
    fields, ct, _ = sealed
    with pytest.raises(DecryptError, match="not readable"):
        _read(session, streams, provider, fields, ct, stream=streams["b"])
