"""Tests for ledger/seal_event.py (D-0003): formula, frozen value, chaining and tamper evidence."""
import hashlib

import pytest

from nacre.ledger.encode_envelope import EnvelopeEncodeError, Purpose, encode_envelope
from nacre.ledger.seal_event import GENESIS_PREV_HASH, SEAL_PREFIX, SealError, seal_event

from test_encode_envelope import seal_values

# Frozen: changing this breaks every existing seal. Computed 2026-09-30 from seal_values().
FROZEN_SEAL = "7fb349a41c3cb09443a17db5a3ce9ed8db355ba0a21e1677b8d08a0ef3a059be"


def test_formula_is_d0003():
    fields = seal_values()
    expected = hashlib.sha256(b"nacre-seal-v1" + fields["prev_hash"] + encode_envelope(fields, Purpose.SEAL)).digest()
    assert SEAL_PREFIX == b"nacre-seal-v1"
    assert seal_event(fields) == expected


def test_frozen_seal():
    assert seal_event(seal_values()).hex() == FROZEN_SEAL


def test_genesis_prev_hash_is_32_zero_bytes():
    assert GENESIS_PREV_HASH == b"\x00" * 32


def test_seal_is_32_bytes():
    assert len(seal_event(seal_values())) == 32


def _chain(n):
    events, prev = [], GENESIS_PREV_HASH
    for seq in range(1, n + 1):
        fields = seal_values(commit_seq=seq, prev_hash=prev, idempotency_key=f"k{seq}",
                             body_ciphertext=f"body-{seq}".encode())
        events.append({**fields, "hash": seal_event(fields)})
        prev = events[-1]["hash"]
    return events


def _recompute_ok(events):
    prev = GENESIS_PREV_HASH
    for e in events:
        fields = {k: v for k, v in e.items() if k != "hash"}
        if e["prev_hash"] != prev or seal_event(fields) != e["hash"]:
            return False
        prev = e["hash"]
    return True


def test_a_clean_chain_recomputes():
    assert _recompute_ok(_chain(5))


@pytest.mark.parametrize("tamper", ["body", "header", "reorder", "drop"])
def test_tampering_breaks_recomputation(tamper):
    events = _chain(5)
    if tamper == "body":
        events[2]["body_ciphertext"] = b"forged"
    elif tamper == "header":
        events[2]["actor_model"] = "other-model"
    elif tamper == "reorder":
        events[1], events[2] = events[2], events[1]
    elif tamper == "drop":
        del events[2]
    assert not _recompute_ok(events)


def test_rewriting_a_whole_chain_with_recomputed_hashes_is_self_consistent():
    # Documents the limit of the chain alone (A-0013): a full rewrite recomputes cleanly.
    # Detecting it is the job of signed checkpoints (D-0003, INDEX #18/#19).
    forged = _chain(3)
    prev = GENESIS_PREV_HASH
    for e in forged:
        e["body_ciphertext"], e["prev_hash"] = b"forged", prev
        e["hash"] = seal_event({k: v for k, v in e.items() if k != "hash"})
        prev = e["hash"]
    assert _recompute_ok(forged)


@pytest.mark.parametrize("prev", [b"\x00" * 31, b"\x00" * 33, bytearray(32), None], ids=["31", "33", "bytearray", "none"])
def test_prev_hash_must_be_32_bytes(prev):
    with pytest.raises(SealError):
        seal_event(seal_values(prev_hash=prev))


def test_hash_itself_is_not_an_input():
    with pytest.raises(EnvelopeEncodeError, match="extra"):
        seal_event({**seal_values(), "hash": b"\x00" * 32})
