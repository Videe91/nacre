"""Tests for ledger/encode_envelope.py (D-0002): exact bytes, exact field sets, no ambiguity."""
import dataclasses
import hashlib
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest

from nacre.core import event as ev
from nacre.ledger.encode_envelope import FIELDS, EnvelopeEncodeError, Purpose, encode_envelope

U1, U2, U3 = UUID(int=1), UUID(int=2), UUID(int=3)
T0 = datetime(2026, 9, 30, 12, 0, 0, 123456, tzinfo=UTC)


def aad_values(**overrides):
    values = {"envelope_version": 1, "event_id": U1, "stream_id": U2, "key_id": U3,
              "event_type": ev.EventType.MESSAGE, "payload_type": ev.PayloadType.TEXT}
    values.update(overrides)
    return values


def seal_values(**overrides):
    values = {
        "envelope_version": 1, "event_id": U1, "stream_id": U2, "org_id": U3, "project_id": None,
        "user_id": None, "agent_id": None, "task_id": None, "commit_seq": 7, "occurred_at": None,
        "occurred_at_basis": None, "occurred_at_precision": None, "recorded_at": T0,
        "committed_at": T0 + timedelta(milliseconds=5), "event_type": ev.EventType.DECISION,
        "payload_type": ev.PayloadType.STRUCTURED, "actor_kind": ev.ActorKind.MODEL, "actor_id": U1,
        "actor_model": "claude-opus-5-5", "actor_model_version": None, "actor_tool": None,
        "source": ev.Source.TOOL, "trust": ev.Trust.TRUSTED, "caused_by": None, "cycle_id": U2,
        "config_version": "cfg-1", "mode": ev.Mode.NORMAL, "key_id": U3, "idempotency_key": "idem-1",
        "request_mac": b"\x11" * 32, "attachment_ref": None, "attachment_sha256": None,
        "body_ciphertext": b"\x01\x01ciphertext", "prev_hash": b"\x00" * 32,
    }
    values.update(overrides)
    return values


def test_aad_bytes_match_the_hand_written_d0002_layout():
    expected = bytes.fromhex(
        "0001"
        + "04" + "00000008" + "0000000000000001"                        # envelope_version = 1
        + "03" + "00000010" + "00000000000000000000000000000001"        # event_id
        + "03" + "00000010" + "00000000000000000000000000000002"        # stream_id
        + "03" + "00000010" + "00000000000000000000000000000003"        # key_id
        + "02" + "00000007" + "message".encode().hex()                  # event_type
        + "02" + "00000004" + "text".encode().hex())                    # payload_type
    assert encode_envelope(aad_values(), Purpose.AAD) == expected


def test_aad_fields_are_exactly_d0002():
    assert FIELDS[(1, Purpose.AAD)] == (
        "envelope_version", "event_id", "stream_id", "key_id", "event_type", "payload_type")


def test_v2_seal_covers_every_envelope_field_except_hash_in_envelope_order():
    names = [f.name for f in dataclasses.fields(ev.Envelope)]
    assert FIELDS[(2, Purpose.SEAL)] == tuple(n for n in names if n != "hash")


def test_v1_layout_is_unchanged_and_v2_only_adds_trust_basis():
    v1, v2 = FIELDS[(1, Purpose.SEAL)], FIELDS[(2, Purpose.SEAL)]
    assert "trust_basis" not in v1 and [f for f in v2 if f != "trust_basis"] == list(v1)
    assert v2.index("trust_basis") == v2.index("key_id") - 1
    assert FIELDS[(2, Purpose.AAD)] == FIELDS[(1, Purpose.AAD)]


def test_frozen_v2_seal_encoding():
    fields = {**seal_values(), "envelope_version": 2, "trust_basis": ev.TrustBasis.ASSERTED}
    assert hashlib.sha256(encode_envelope(fields, Purpose.SEAL)).hexdigest() == FROZEN_V2_SEAL_SHA256


def test_frozen_seal_encoding():
    # Frozen: if this digest changes, every existing seal in every ledger breaks.
    digest = hashlib.sha256(encode_envelope(seal_values(), Purpose.SEAL)).hexdigest()
    assert digest == FROZEN_SEAL_SHA256


def test_timestamp_encodes_the_instant_in_microseconds():
    plus_two = T0.astimezone(timezone(timedelta(hours=2)))
    a = encode_envelope(seal_values(recorded_at=T0), Purpose.SEAL)
    assert a == encode_envelope(seal_values(recorded_at=plus_two), Purpose.SEAL)
    micros = (T0 - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(microseconds=1)
    assert bytes.fromhex("05" + "00000008") + micros.to_bytes(8, "big") in a
    assert a != encode_envelope(seal_values(recorded_at=T0 + timedelta(microseconds=1)), Purpose.SEAL)


def test_null_empty_and_absent_are_distinct():
    none = encode_envelope(seal_values(actor_tool=None), Purpose.SEAL)
    empty = encode_envelope(seal_values(actor_tool=""), Purpose.SEAL)
    assert none != empty


def test_length_prefix_removes_boundary_ambiguity():
    a = encode_envelope(seal_values(actor_model="ab", actor_model_version="c"), Purpose.SEAL)
    b = encode_envelope(seal_values(actor_model="a", actor_model_version="bc"), Purpose.SEAL)
    assert a != b


def test_every_field_changes_the_seal_encoding():
    base = encode_envelope(seal_values(), Purpose.SEAL)
    alternatives = {
        "envelope_version": None, "commit_seq": 8, "occurred_at": T0, "recorded_at": T0 + timedelta(seconds=1),
        "committed_at": T0, "request_mac": b"\x12" * 32, "body_ciphertext": b"x", "prev_hash": b"\x01" * 32,
        "attachment_ref": b"\x02" * 32, "attachment_sha256": b"\x03" * 32, "idempotency_key": "idem-2",
    }
    for name in FIELDS[(1, Purpose.SEAL)]:
        if name == "envelope_version":
            continue  # a different version is a different layout, tested separately
        value = seal_values()[name]
        if name in alternatives:
            other = alternatives[name]
        elif isinstance(value, UUID) or (value is None and (name.endswith("_id") or name == "caused_by")):
            other = UUID(int=99)
        else:
            other = "x-changed"
        assert encode_envelope(seal_values(**{name: other}), Purpose.SEAL) != base, name


@pytest.mark.parametrize("values,match", [
    ({**aad_values(), "hash": b"x"}, "extra"),
    ({k: v for k, v in aad_values().items() if k != "key_id"}, "missing"),
    (aad_values(envelope_version=3), "unknown envelope_version"),
    (aad_values(envelope_version=True), "unknown envelope_version"),
    (aad_values(event_id=str(U1)), "does not fit"),
    (aad_values(event_type=b"message"), "does not fit"),
], ids=["extra-field", "missing-field", "version-3", "bool-version", "uuid-as-text", "enum-as-bytes"])
def test_rejects_bad_aad_inputs(values, match):
    with pytest.raises(EnvelopeEncodeError, match=match):
        encode_envelope(values, Purpose.AAD)


@pytest.mark.parametrize("field,value", [
    ("recorded_at", datetime(2026, 9, 30, 12, 0)),        # naive datetime
    ("commit_seq", True),                                  # bool is not an int here
    ("commit_seq", 2**63),                                 # beyond int64
    ("request_mac", bytearray(b"\x11" * 32)),              # only bytes
    ("actor_model", "\ud800"),                             # lone surrogate
], ids=["naive-time", "bool-int", "int64-overflow", "bytearray", "surrogate"])
def test_rejects_bad_seal_values(field, value):
    with pytest.raises(EnvelopeEncodeError):
        encode_envelope(seal_values(**{field: value}), Purpose.SEAL)


FROZEN_SEAL_SHA256 = "bd097850337d9285de0d8fe07b70c89c0ed04816427389169ea7b03df9dda5ee"  # 442 bytes, frozen 2026-09-30

FROZEN_V2_SEAL_SHA256 = "4b7e2e5d59c504b6b0055715025cda92aaf4e4d54460bff98c9dbb492140546a"  # frozen 2026-09-30
