"""
Functionality: Turn envelope fields into canonical bytes: the one encoding the seal and the AAD use.
Owns: per-version field order, per-field value kinds, the tag-length-value byte format.
Public entry: encode_envelope()
Decisions: D-0002, D-0003, D-0008, D-0012
Assumptions: none
Notes: Format (D-0002 option b): uint16 envelope_version, then for each field in the fixed order:
  tag(1) | length(4, big-endian) | value bytes. Tags:
    0x00 null (length 0)          0x01 bytes             0x02 text (UTF-8; enums use their value)
    0x03 uuid (16 raw bytes)      0x04 int (int64 BE)    0x05 timestamp (int64 BE microseconds since the Unix epoch, UTC)
  Two purposes, each with an exact field list:
    SEAL — every Envelope field of that version except `hash` (D-0003). v2 adds trust_basis before key_id.
    AAD — envelope_version, event_id, stream_id, key_id, event_type, payload_type (D-0002, D-0008).
  The caller passes exactly the purpose's fields, no more and no fewer, so a field added to the
  envelope can never be silently left out of the seal (a test also pins SEAL to core.event.Envelope).
  Naive datetimes are rejected; aware ones encode by instant, so the time zone does not matter.
  Field order and kinds for a version never change; a new layout needs a new envelope_version.
"""
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from enum import Enum, StrEnum
from uuid import UUID

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_ONE_MICROSECOND = timedelta(microseconds=1)
_INT64 = (-(2**63), 2**63 - 1)
_MAX_LENGTH = 2**32 - 1

_NULL, _BYTES, _TEXT, _UUID, _INT, _TIME = range(6)

# Version 1: field -> kind, in canonical order (the D-0002 table order).
_FIELDS_V1: dict[str, int] = {
    "envelope_version": _INT, "event_id": _UUID, "stream_id": _UUID,
    "org_id": _UUID, "project_id": _UUID, "user_id": _UUID, "agent_id": _UUID, "task_id": _UUID,
    "commit_seq": _INT, "occurred_at": _TIME, "occurred_at_basis": _TEXT, "occurred_at_precision": _TEXT,
    "recorded_at": _TIME, "committed_at": _TIME, "event_type": _TEXT, "payload_type": _TEXT,
    "actor_kind": _TEXT, "actor_id": _UUID, "actor_model": _TEXT, "actor_model_version": _TEXT,
    "actor_tool": _TEXT, "source": _TEXT, "trust": _TEXT, "caused_by": _UUID, "cycle_id": _UUID,
    "config_version": _TEXT, "mode": _TEXT, "key_id": _UUID, "idempotency_key": _TEXT,
    "request_mac": _BYTES, "attachment_ref": _BYTES, "attachment_sha256": _BYTES,
    "body_ciphertext": _BYTES, "prev_hash": _BYTES,
}


class Purpose(StrEnum):
    SEAL = "seal"
    AAD = "aad"


# Version 2 (D-0002 amendment 4): v1 plus trust_basis, just before key_id. v1 is frozen and never edited.
_FIELDS_V2: dict[str, int] = {}
for _name, _kind in _FIELDS_V1.items():
    if _name == "key_id":
        _FIELDS_V2["trust_basis"] = _TEXT
    _FIELDS_V2[_name] = _kind

_KINDS = {**_FIELDS_V1, **_FIELDS_V2}
_AAD = ("envelope_version", "event_id", "stream_id", "key_id", "event_type", "payload_type")

FIELDS: dict[tuple[int, Purpose], tuple[str, ...]] = {
    (1, Purpose.SEAL): tuple(_FIELDS_V1),
    (1, Purpose.AAD): _AAD,
    (2, Purpose.SEAL): tuple(_FIELDS_V2),
    (2, Purpose.AAD): _AAD,
}


class EnvelopeEncodeError(ValueError):
    """The fields cannot be canonically encoded (wrong set, wrong kind, or unknown version)."""


def encode_envelope(values: Mapping[str, object], purpose: Purpose) -> bytes:
    """Canonical bytes of `values` for `purpose`. `values` must hold exactly that purpose's fields."""
    version = values.get("envelope_version")
    if type(version) is not int or (version, purpose) not in FIELDS:
        raise EnvelopeEncodeError(f"unknown envelope_version {version!r} for purpose {purpose}")
    order = FIELDS[(version, purpose)]
    if set(values) != set(order):
        missing, extra = set(order) - set(values), set(values) - set(order)
        raise EnvelopeEncodeError(f"{purpose} fields mismatch: missing {sorted(missing)}, extra {sorted(extra)}")
    out = bytearray(version.to_bytes(2, "big"))
    for name in order:
        tag, raw = _value_bytes(name, _KINDS[name], values[name])
        if len(raw) > _MAX_LENGTH:
            raise EnvelopeEncodeError(f"{name} is longer than {_MAX_LENGTH} bytes")
        out.append(tag)
        out += len(raw).to_bytes(4, "big")
        out += raw
    return bytes(out)


def _value_bytes(name: str, kind: int, value: object) -> tuple[int, bytes]:
    if value is None:
        return _NULL, b""
    if kind == _UUID and type(value) is UUID:
        return _UUID, value.bytes
    if kind == _INT and type(value) is int and _INT64[0] <= value <= _INT64[1]:
        return _INT, value.to_bytes(8, "big", signed=True)
    if kind == _TIME and type(value) is datetime and value.utcoffset() is not None:
        return _TIME, ((value - _EPOCH) // _ONE_MICROSECOND).to_bytes(8, "big", signed=True)
    if kind == _TEXT and isinstance(value, str):
        text = value.value if isinstance(value, Enum) else value
        try:
            return _TEXT, text.encode("utf-8")
        except UnicodeEncodeError:
            raise EnvelopeEncodeError(f"{name} is not valid Unicode") from None
    if kind == _BYTES and type(value) is bytes:
        return _BYTES, value
    raise EnvelopeEncodeError(f"{name}: {type(value).__name__} value {value!r:.40} does not fit this field")
