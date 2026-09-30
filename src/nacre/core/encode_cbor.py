"""
Functionality: Encode a value as deterministic CBOR (RFC 8949 §4.2.1), restricted to Nacre's subset.
Owns: the allowed-type check, shortest-form heads, bytewise map-key ordering, the depth limit.
Public entry: encode_cbor()
Decisions: D-0008
Assumptions: none
Notes: Subset (D-0008 amendment 4): dict with str keys, list, str, bytes, int in [-2^64, 2^64-1],
  bool, None. Everything else is rejected: floats, tuples, bytearray, sets, tags, undefined.
  bool is checked before int because bool is a subclass of int in Python.
  Map keys are ordered by the bytewise order of their encodings (RFC 8949 §4.2.1); for text keys
  this equals shorter-first then lexicographic by UTF-8 bytes.
  MAX_DEPTH = 64 nested containers (D1), shared with core/decode_cbor.py.
  These bytes feed request_mac, so the output for a given value must never change.
"""

MAX_DEPTH = 64
_UINT64_MAX = 2**64 - 1


class CborEncodeError(ValueError):
    """The value is outside Nacre's deterministic CBOR subset."""


def encode_cbor(value: object) -> bytes:
    """Deterministic CBOR bytes for `value`. Raises CborEncodeError for anything outside the subset."""
    out = bytearray()
    _encode(value, out, 0)
    return bytes(out)


def _head(major: int, arg: int, out: bytearray) -> None:
    if arg < 24:
        out.append(major << 5 | arg)
    elif arg <= 0xFF:
        out.append(major << 5 | 24)
        out += arg.to_bytes(1, "big")
    elif arg <= 0xFFFF:
        out.append(major << 5 | 25)
        out += arg.to_bytes(2, "big")
    elif arg <= 0xFFFF_FFFF:
        out.append(major << 5 | 26)
        out += arg.to_bytes(4, "big")
    else:
        out.append(major << 5 | 27)
        out += arg.to_bytes(8, "big")


def _encode(value: object, out: bytearray, depth: int) -> None:
    if value is None:
        out.append(0xF6)
    elif value is False:
        out.append(0xF4)
    elif value is True:
        out.append(0xF5)
    elif type(value) is int:
        if 0 <= value <= _UINT64_MAX:
            _head(0, value, out)
        elif -_UINT64_MAX - 1 <= value < 0:
            _head(1, -1 - value, out)
        else:
            raise CborEncodeError(f"integer {value} is outside [-2^64, 2^64-1]")
    elif type(value) is bytes:
        _head(2, len(value), out)
        out += value
    elif type(value) is str:
        try:
            data = value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise CborEncodeError(f"text is not valid Unicode: {exc}") from None
        _head(3, len(data), out)
        out += data
    elif type(value) is list:
        _enter(depth)
        _head(4, len(value), out)
        for item in value:
            _encode(item, out, depth + 1)
    elif type(value) is dict:
        _enter(depth)
        entries = []
        for key, item in value.items():
            if type(key) is not str:
                raise CborEncodeError(f"map keys must be text, got {type(key).__name__}")
            encoded_key = bytearray()
            _encode(key, encoded_key, depth + 1)
            entries.append((bytes(encoded_key), item))
        entries.sort(key=lambda entry: entry[0])
        _head(5, len(entries), out)
        for encoded_key, item in entries:
            out += encoded_key
            _encode(item, out, depth + 1)
    else:
        raise CborEncodeError(f"type {type(value).__name__} is outside the subset (no floats, tuples, sets, tags)")


def _enter(depth: int) -> None:
    if depth >= MAX_DEPTH:
        raise CborEncodeError(f"nesting deeper than {MAX_DEPTH}")
