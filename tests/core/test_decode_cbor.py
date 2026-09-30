"""Tests for core/decode_cbor.py (D-0008): strict rejection, round-trips, cbor2 cross-check, fuzzing."""
import cbor2
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from nacre.core.decode_cbor import CborDecodeError, decode_cbor
from nacre.core.encode_cbor import MAX_DEPTH, encode_cbor

# Reuse the encoder's vectors: every canonical vector must decode back to its value.
from test_encode_cbor import BOUNDARIES, FROZEN, RFC_8949_APPENDIX_A, subset_values

REJECT = {
    "empty input": "",
    "non-shortest 0 (1-byte arg)": "1800",
    "non-shortest 23": "1817",
    "non-shortest 255 (2-byte arg)": "1900ff",
    "non-shortest 65535 (4-byte arg)": "1a0000ffff",
    "non-shortest 2^32-1 (8-byte arg)": "1b00000000ffffffff",
    "non-shortest -1": "3800",
    "non-shortest text length": "780161",
    "non-shortest array length": "9800",
    "reserved additional info": "1c",
    "indefinite array": "9fff",
    "indefinite text": "7f6161ff",
    "indefinite map": "bfff",
    "break byte": "ff",
    "half float 1.0": "f93c00",
    "single float": "fa3f800000",
    "double float": "fb3ff0000000000000",
    "undefined": "f7",
    "simple value 16": "f0",
    "simple value, 1-byte form": "f820",
    "tag 0 datetime": "c074323031332d30332d32315432303a30343a30305a",
    "tag 2 bignum": "c249010000000000000000",
    "map out of order": "a2616201616102",
    "map duplicate key": "a2616101616102",
    "map longer key first": "a262616101616202",
    "map integer key": "a10102",
    "map bytes key": "a1416101",
    "trailing byte": "0000",
    "truncated argument": "1903",
    "truncated text": "6461",
    "bytes length beyond input": "5affffffff",
    "array count beyond input": "9bffffffffffffffff",
    "map count beyond input": "bbffffffffffffffff",
    "map cut off mid-entry": "a2616101",
    "invalid UTF-8": "62c328",
    "UTF-8 surrogate": "63eda080",
}


@pytest.mark.parametrize("value,hex_", RFC_8949_APPENDIX_A + BOUNDARIES + FROZEN, ids=lambda v: repr(v)[:30])
def test_canonical_vectors_decode(value, hex_):
    assert decode_cbor(bytes.fromhex(hex_)) == value


@pytest.mark.parametrize("hex_", REJECT.values(), ids=REJECT.keys())
def test_rejects(hex_):
    with pytest.raises(CborDecodeError):
        decode_cbor(bytes.fromhex(hex_))


def test_rejects_non_bytes_input():
    with pytest.raises(CborDecodeError):
        decode_cbor(bytearray(b"\x00"))


def test_nesting_limit():
    at_limit = b"\x81" * (MAX_DEPTH - 1) + b"\x80"
    assert decode_cbor(at_limit) is not None
    with pytest.raises(CborDecodeError, match="nesting"):
        decode_cbor(b"\x81" + at_limit)


def test_deep_nesting_bomb_is_rejected_without_recursion_error():
    with pytest.raises(CborDecodeError, match="nesting"):
        decode_cbor(b"\x81" * 100_000 + b"\x80")


@settings(max_examples=500)
@given(subset_values)
def test_round_trip(value):
    assert decode_cbor(encode_cbor(value)) == value


@settings(max_examples=500)
@given(subset_values)
def test_decodes_cbor2_canonical_bytes(value):
    assert decode_cbor(cbor2.dumps(value, canonical=True)) == value


@settings(max_examples=300)
@given(subset_values.filter(lambda v: isinstance(v, (dict, list)) and len(v) >= 2))
def test_non_canonical_cbor2_output_is_rejected_unless_already_canonical(value):
    # cbor2's default (non-canonical) encoder keeps insertion order; reversed maps are then non-canonical.
    def reorder(v):
        if isinstance(v, dict):
            return {k: reorder(v[k]) for k in reversed(list(v))}
        if isinstance(v, list):
            return [reorder(x) for x in v]
        return v
    raw = cbor2.dumps(reorder(value))
    if raw == encode_cbor(value):
        assert decode_cbor(raw) == value
    else:
        with pytest.raises(CborDecodeError):
            decode_cbor(raw)


@settings(max_examples=2000)
@given(st.binary(max_size=64))
def test_fuzz_either_canonical_or_clean_rejection(data):
    # Arbitrary bytes: decoding either succeeds on a canonical item, or raises CborDecodeError only.
    try:
        value = decode_cbor(data)
    except CborDecodeError:
        return
    assert encode_cbor(value) == data
