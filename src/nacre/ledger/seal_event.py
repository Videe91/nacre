"""
Functionality: Compute an event's seal: the hash that chains it to the previous event of its stream.
Owns: the seal formula, its domain-separation prefix, the genesis prev_hash.
Public entry: seal_event()
Decisions: D-0003
Assumptions: A-0013
Notes: hash = SHA-256("nacre-seal-v1" | prev_hash | encode_envelope(fields, SEAL)) (D-0003).
  prev_hash appears twice (prefix and inside the encoding); that is the D-0003 formula, kept as written.
  The seal covers ciphertext, never plaintext, so it needs no keys and survives shredding.
  The first event of a stream chains to GENESIS_PREV_HASH (32 zero bytes), which the database's
  linkage trigger also requires (schema/sql/0001_ledger.sql).
"""
import hashlib
from collections.abc import Mapping

from nacre.ledger.encode_envelope import Purpose, encode_envelope

SEAL_PREFIX = b"nacre-seal-v1"
GENESIS_PREV_HASH = bytes(32)


class SealError(ValueError):
    """The fields cannot be sealed."""


def seal_event(fields: Mapping[str, object]) -> bytes:
    """The 32-byte seal of an event, from every envelope field except `hash`."""
    prev_hash = fields.get("prev_hash")
    if type(prev_hash) is not bytes or len(prev_hash) != 32:
        raise SealError("prev_hash must be exactly 32 bytes")
    return hashlib.sha256(SEAL_PREFIX + prev_hash + encode_envelope(fields, Purpose.SEAL)).digest()
