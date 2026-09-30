"""Tests for keys/encrypt_payload.py (D-0008): format, AAD binding, use count and cap, MAC sub-keys, body rules."""
import uuid
from datetime import date

import psycopg
import pytest

from nacre.core.event import EventType, PayloadType, new_event_id
from nacre.keys.encrypt_payload import (EncryptError, KeyExhausted, MacPurpose, derive_mac, encrypt_payload)
from nacre.keys.get_or_create_key import get_or_create_key

SEPT = date(2026, 9, 1)
BODY = {"content_version": 1, "content": "the build is green", "source_ref": "ci:run-42"}


@pytest.fixture
def key(session, streams, provider):
    """A committed data key (created in its own session so later sessions and admin can see it)."""
    a = streams["a"]
    with session(uuid.uuid4(), read=[a], write=[a]) as s:
        return get_or_create_key(s.conn, provider, a, a, SEPT)


@pytest.fixture
def writer(session, streams, key):
    a = streams["a"]
    with session(uuid.uuid4(), read=[a], write=[a]) as s:
        yield s, key


def aad(key, **overrides):
    fields = {"envelope_version": 1, "event_id": new_event_id(), "stream_id": key.stream_id, "key_id": key.key_id,
              "event_type": EventType.MESSAGE, "payload_type": PayloadType.TEXT}
    fields.update(overrides)
    return fields


def test_header_layout(writer):
    s, key = writer
    ct = encrypt_payload(s.conn, key, aad(key), BODY)
    assert ct[:3] == b"\x01\x01\x00"
    assert ct[3:19] == key.key_id.bytes
    assert len(ct) > 31 + 16
    assert b"the build is green" not in ct


def test_nonces_are_fresh(writer):
    s, key = writer
    fields = aad(key)
    assert encrypt_payload(s.conn, key, fields, BODY) != encrypt_payload(s.conn, key, fields, BODY)


def test_each_encryption_is_counted(writer, streams):
    s, key = writer
    for _ in range(3):
        encrypt_payload(s.conn, key, aad(key), BODY)
    assert s.conn.execute("SELECT encryption_count FROM keys.data_keys WHERE key_id = %s", (key.key_id,)).fetchone()[0] == 3


def test_cap_raises_key_exhausted_and_leaves_the_transaction_usable(writer, streams):
    s, key = writer
    with psycopg.connect(streams["dsn"]["admin"]) as admin:
        admin.execute("UPDATE keys.data_keys SET encryption_count = 268435456 WHERE key_id = %s", (key.key_id,))
    with pytest.raises(KeyExhausted):
        encrypt_payload(s.conn, key, aad(key), BODY)
    assert s.conn.execute("SELECT 1").fetchone() == (1,)


def test_aad_must_name_this_key_and_stream(writer):
    s, key = writer
    with pytest.raises(EncryptError, match="do not match"):
        encrypt_payload(s.conn, key, aad(key, key_id=uuid.uuid4()), BODY)
    with pytest.raises(EncryptError, match="do not match"):
        encrypt_payload(s.conn, key, aad(key, stream_id=uuid.uuid4()), BODY)


@pytest.mark.parametrize("body,match", [
    ({"content": "x"}, "content_version"),
    ({"content_version": 0, "content": "x"}, "content_version"),
    ({"content_version": 1}, "content is required"),
    ({"content_version": 1, "content": "x", "extra": 1}, "unknown body keys"),
    ({"content_version": 1, "content": "x", "person": {"name": "Ada", "age": "36"}}, "person"),
    ({"content_version": 1, "content": "x", "attachment": {"description": 3}}, "attachment"),
    ({"content_version": 1, "content": "x", "redactions": ["ok", 7]}, "redactions"),
    ({"content_version": 1, "content": "x", "public_credentials": "stripe-publishable"}, "public_credentials"),
    ({"content_version": 1, "content": "x", "source_ref": 5}, "source_ref"),
    (["not", "a", "map"], "must be a dict"),
], ids=["no-version", "version-0", "no-content", "extra-key", "person-key", "attachment-type", "redaction-type", "public-cred-type",
        "source-type", "not-a-map"])
def test_body_rules(writer, body, match):
    s, key = writer
    with pytest.raises(EncryptError, match=match):
        encrypt_payload(s.conn, key, aad(key), body)


def test_floats_in_content_are_rejected_by_the_codec(writer):
    s, key = writer
    with pytest.raises(ValueError):
        encrypt_payload(s.conn, key, aad(key), {"content_version": 1, "content": {"score": 0.5}})


def test_read_only_session_cannot_encrypt(session, streams, key):
    with session(uuid.uuid4(), read=[streams["a"]]) as s:
        with pytest.raises(EncryptError, match="not writable"):
            encrypt_payload(s.conn, key, aad(key), BODY)


def test_macs_are_deterministic_per_key_and_purpose(writer, streams, provider):
    _, key = writer
    m1 = derive_mac(key, MacPurpose.REQUEST_MAC, b"request")
    assert m1 == derive_mac(key, MacPurpose.REQUEST_MAC, b"request") and len(m1) == 32
    assert m1 != derive_mac(key, MacPurpose.ATTACHMENT_REF, b"request")
    assert m1 != derive_mac(key, MacPurpose.REQUEST_MAC, b"request2")
    s, _ = writer   # same session: a second session would wait on this one's locks
    other = get_or_create_key(s.conn, provider, streams["a"], uuid.uuid4(), SEPT)
    assert m1 != derive_mac(other, MacPurpose.REQUEST_MAC, b"request")


def test_unknown_mac_purpose(writer):
    _, key = writer
    with pytest.raises(EncryptError):
        derive_mac(key, "request_mac", b"x")


def test_public_credentials_is_optional_and_absence_encodes_as_before():
    # D-0008 amendment 5, owner condition: a body without the key is byte-identical to before.
    from nacre.core.encode_cbor import encode_cbor
    body = {"content_version": 1, "content": "the build is green", "source_ref": "ci:run-42"}
    assert encode_cbor(body).hex() == "a367636f6e74656e7472746865206275696c6420697320677265656e6a736f757263655f7265666963693a72756e2d34326f636f6e74656e745f76657273696f6e01"   # frozen before the amendment
    tagged = {**body, "public_credentials": ["stripe-publishable"]}
    assert encode_cbor(tagged) != encode_cbor(body)


def test_tagged_body_round_trips(writer):
    s, key = writer
    ct = encrypt_payload(s.conn, key, aad(key), {**BODY, "public_credentials": ["sentry-dsn-public"]})
    assert ct[:3] == b"\x01\x01\x00"
