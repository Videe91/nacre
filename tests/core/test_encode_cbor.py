"""Tests for core/encode_cbor.py (D-0008): RFC vectors, frozen Nacre vectors, cbor2 cross-check, hypothesis."""
import cbor2
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from nacre.core.encode_cbor import MAX_DEPTH, CborEncodeError, encode_cbor

# RFC 8949 Appendix A, every example inside Nacre's subset (no floats, tags, simple values, indefinite).
RFC_8949_APPENDIX_A = [
    (0, "00"), (1, "01"), (10, "0a"), (23, "17"), (24, "1818"), (25, "1819"), (100, "1864"),
    (1000, "1903e8"), (1000000, "1a000f4240"), (1000000000000, "1b000000e8d4a51000"),
    (18446744073709551615, "1bffffffffffffffff"), (-18446744073709551616, "3bffffffffffffffff"),
    (-1, "20"), (-10, "29"), (-100, "3863"), (-1000, "3903e7"),
    (False, "f4"), (True, "f5"), (None, "f6"),
    (b"", "40"), (bytes.fromhex("01020304"), "4401020304"),
    ("", "60"), ("a", "6161"), ("IETF", "6449455446"), ('"\\', "62225c"),
    ("ü", "62c3bc"), ("水", "63e6b0b4"), ("\U00010151", "64f0908591"),
    ([], "80"), ([1, 2, 3], "83010203"), ([1, [2, 3], [4, 5]], "8301820203820405"),
    (list(range(1, 26)), "98190102030405060708090a0b0c0d0e0f101112131415161718181819"),
    ({}, "a0"), ({"a": 1, "b": [2, 3]}, "a26161016162820203"), (["a", {"b": "c"}], "826161a161626163"),
    ({"a": "A", "b": "B", "c": "C", "d": "D", "e": "E"}, "a56161614161626142616361436164614461656145"),
]

# Frozen Nacre vectors. These bytes feed request_mac: they must never change.
FROZEN = [
    ({"b": 1, "a": 2, "aa": 3}, "a361610261620162616103"),
    ({"content_version": 1, "content": "hello nacre", "redactions": ["aws-access-token"],
      "source_ref": "git:9f2c1e0", "person": {"name": "Ada", "email": None}},
     "a566706572736f6ea2646e616d656341646165656d61696cf667636f6e74656e746b68656c6c6f206e616372656a7265"
     "64616374696f6e7381706177732d6163636573732d746f6b656e6a736f757263655f7265666b6769743a39663263316530"
     "6f636f6e74656e745f76657273696f6e01"),
]

# Every shortest-form boundary of the head argument, for ints and lengths (not left to hypothesis).
BOUNDARIES = [
    (23, "17"), (24, "1818"), (255, "18ff"), (256, "190100"), (65535, "19ffff"), (65536, "1a00010000"),
    (4294967295, "1affffffff"), (4294967296, "1b0000000100000000"),
    (-24, "37"), (-25, "3818"), (-256, "38ff"), (-257, "390100"), (-65536, "39ffff"), (-65537, "3a00010000"),
    (b"\x00" * 23, "57" + "00" * 23), (b"\x00" * 24, "5818" + "00" * 24),
    ("x" * 255, "78ff" + "78" * 255), ("x" * 256, "790100" + "78" * 256),
    ([None] * 24, "9818" + "f6" * 24),
]

subset_values = st.recursive(
    st.none() | st.booleans() | st.integers(min_value=-(2**64), max_value=2**64 - 1)
    | st.text() | st.binary(),
    lambda children: st.lists(children, max_size=5) | st.dictionaries(st.text(), children, max_size=5),
    max_leaves=25,
)


@pytest.mark.parametrize("value,hex_", RFC_8949_APPENDIX_A, ids=lambda v: repr(v)[:30])
def test_rfc_8949_appendix_a_vectors(value, hex_):
    assert encode_cbor(value).hex() == hex_


@pytest.mark.parametrize("value,hex_", BOUNDARIES, ids=lambda v: repr(v)[:20])
def test_shortest_form_boundaries(value, hex_):
    assert encode_cbor(value).hex() == hex_


@pytest.mark.parametrize("value,hex_", FROZEN, ids=["key-order", "nacre-body"])
def test_frozen_nacre_vectors(value, hex_):
    assert encode_cbor(value).hex() == hex_


def test_map_keys_sort_bytewise_by_encoding_not_insertion():
    assert encode_cbor({"b": 1, "a": 2}) == encode_cbor({"a": 2, "b": 1})
    # "aa" encodes as 62 61 61, after "b" (61 62): shorter keys first.
    assert list(cbor2.loads(encode_cbor({"aa": 1, "b": 2, "a": 3}))) == ["a", "b", "aa"]


def test_bool_is_not_encoded_as_int():
    assert encode_cbor(True) == b"\xf5" and encode_cbor(1) == b"\x01"


@pytest.mark.parametrize("value", [
    1.0, float("nan"), 2**64, -(2**64) - 1, (1, 2), {1, 2}, bytearray(b"x"), frozenset(),
    {1: "non-text key"}, {b"k": 1}, [1.5], {"a": 0.5}, "\ud800",
], ids=repr)
def test_out_of_subset_values_are_rejected(value):
    with pytest.raises(CborEncodeError):
        encode_cbor(value)


def test_nesting_limit():
    value = []
    for _ in range(MAX_DEPTH - 1):
        value = [value]
    encode_cbor(value)  # MAX_DEPTH containers: allowed
    with pytest.raises(CborEncodeError, match="nesting"):
        encode_cbor([value])


@settings(max_examples=500)
@given(subset_values)
def test_cbor2_decodes_our_bytes_to_the_same_value(value):
    assert cbor2.loads(encode_cbor(value)) == value


@settings(max_examples=500)
@given(subset_values)
def test_matches_cbor2_canonical_encoding_on_the_subset(value):
    assert encode_cbor(value) == cbor2.dumps(value, canonical=True)


@given(subset_values)
def test_encoding_is_deterministic(value):
    assert encode_cbor(value) == encode_cbor(value)
