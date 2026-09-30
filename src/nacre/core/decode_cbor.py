"""
Functionality: Strictly decode deterministic CBOR in Nacre's subset, rejecting anything else.
Owns: head parsing, shortest-form and ordering checks, subset enforcement, bounds and depth limits.
Public entry: decode_cbor()
Decisions: D-0008
Assumptions: none
Notes: Input may be untrusted (it is decrypted ciphertext, but decoding must never be the weak
  point). The decoder rejects:
  - reserved or indefinite-length heads, and non-shortest arguments;
  - tags, floats, simple values other than false/true/null;
  - non-text map keys, keys not strictly ascending by encoded bytes (which also rules out duplicates);
  - invalid UTF-8, lengths running past the input, trailing bytes;
  - nesting deeper than MAX_DEPTH (shared with core/encode_cbor.py).
  Lengths are checked against the remaining input before any allocation.
  Final guard (D-0008): the result is re-encoded and must equal the input byte for byte.
  Only CborDecodeError ever escapes for bad input.
"""
from nacre.core.encode_cbor import MAX_DEPTH, encode_cbor


class CborDecodeError(ValueError):
    """The bytes are not canonical CBOR in Nacre's subset."""


def decode_cbor(data: bytes) -> object:
    """Decode `data`, which must be exactly one canonical item in Nacre's CBOR subset."""
    if type(data) is not bytes:
        raise CborDecodeError(f"expected bytes, got {type(data).__name__}")
    value, end = _item(data, 0, 0)
    if end != len(data):
        raise CborDecodeError(f"{len(data) - end} trailing byte(s) after the item")
    if encode_cbor(value) != data:  # unreachable if the checks above are complete; kept as a guard
        raise CborDecodeError("input is not the canonical encoding of its value")
    return value


def _take(data: bytes, pos: int, n: int) -> bytes:
    if n > len(data) - pos:
        raise CborDecodeError(f"needs {n} byte(s) at offset {pos}, only {len(data) - pos} left")
    return data[pos:pos + n]


def _head(data: bytes, pos: int) -> tuple[int, int, int]:
    initial = _take(data, pos, 1)[0]
    major, info = initial >> 5, initial & 0x1F
    pos += 1
    if major == 7:
        return major, info, pos  # simple values and floats: interpreted by _item
    if info < 24:
        return major, info, pos
    if info > 27:
        raise CborDecodeError(f"reserved or indefinite-length head 0x{initial:02x} at offset {pos - 1}")
    size = 1 << (info - 24)
    arg = int.from_bytes(_take(data, pos, size), "big")
    minimum = 24 if size == 1 else 1 << (4 * size)  # smallest value that needs this width
    if arg < minimum:
        raise CborDecodeError(f"non-shortest argument at offset {pos - 1}")
    return major, arg, pos + size


def _item(data: bytes, pos: int, depth: int) -> tuple[object, int]:
    start = pos
    major, arg, pos = _head(data, pos)
    if major == 0:
        return arg, pos
    if major == 1:
        return -1 - arg, pos
    if major == 2:
        return _take(data, pos, arg), pos + arg
    if major == 3:
        raw = _take(data, pos, arg)
        try:
            return raw.decode("utf-8"), pos + arg
        except UnicodeDecodeError:
            raise CborDecodeError(f"invalid UTF-8 in text at offset {start}") from None
    if major == 4:
        _enter(depth)
        if arg > len(data) - pos:  # each item takes at least one byte
            raise CborDecodeError(f"array of {arg} items cannot fit at offset {start}")
        items = []
        for _ in range(arg):
            item, pos = _item(data, pos, depth + 1)
            items.append(item)
        return items, pos
    if major == 5:
        _enter(depth)
        if arg > (len(data) - pos) // 2:  # each entry takes at least two bytes
            raise CborDecodeError(f"map of {arg} entries cannot fit at offset {start}")
        result, previous_key = {}, None
        for _ in range(arg):
            key_start = pos
            if _take(data, pos, 1)[0] >> 5 != 3:
                raise CborDecodeError(f"map key at offset {pos} is not text")
            key, pos = _item(data, pos, depth + 1)
            encoded_key = data[key_start:pos]
            if previous_key is not None and encoded_key <= previous_key:
                raise CborDecodeError(f"map key at offset {key_start} is duplicate or out of canonical order")
            previous_key = encoded_key
            result[key], pos = _item(data, pos, depth + 1)
        return result, pos
    if major == 6:
        raise CborDecodeError(f"tags are outside the subset (offset {start})")
    if arg == 20:
        return False, pos
    if arg == 21:
        return True, pos
    if arg == 22:
        return None, pos
    raise CborDecodeError(f"simple value or float 0x{data[start]:02x} is outside the subset (offset {start})")


def _enter(depth: int) -> None:
    if depth >= MAX_DEPTH:
        raise CborDecodeError(f"nesting deeper than {MAX_DEPTH}")
