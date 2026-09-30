"""
Functionality: Decrypt an event body, or report it as shredded when its key no longer exists.
Owns: ciphertext header parsing and checks, the readability check on the stream, AEAD
  verification, strict body decoding, and the Shredded result.
Public entry: decrypt_payload(), Shredded
Decisions: D-0004, D-0005, D-0008
Assumptions: none
Notes: Returns the body dict, or Shredded(key_id) when the data key or its stream master key is gone
  (D-0004: shredded reads are an explicit result, never an error and never partial plaintext).
  "Key missing" only means shredded when the stream is readable in this session; otherwise RLS
  would hide a live key. So an unreadable stream is refused, never reported as Shredded. (D1)
  Rejected, never partially decrypted: an unknown version or algorithm, any flag bit set (the
  compression flag is reserved in v1), a header key_id different from the envelope's, a failed
  AEAD check, or non-canonical / out-of-subset CBOR.
"""
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

import psycopg
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from nacre.core.decode_cbor import CborDecodeError, decode_cbor
from nacre.core.root_key_provider import RootKeyProvider
from nacre.keys.get_or_create_key import load_key
from nacre.ledger.encode_envelope import Purpose, encode_envelope

_HEADER = 3 + 16 + 12
_TAG = 16


@dataclass(frozen=True, slots=True)
class Shredded:
    key_id: UUID


class DecryptError(ValueError):
    """The ciphertext is malformed, tampered, or not readable in this session."""


def decrypt_payload(conn: psycopg.Connection, provider: RootKeyProvider,
                    aad_fields: Mapping[str, object], ciphertext: bytes) -> dict | Shredded:
    """The decrypted body of one event, or Shredded if its key has been destroyed."""
    if type(ciphertext) is not bytes or len(ciphertext) < _HEADER + _TAG:
        raise DecryptError("ciphertext shorter than header + tag")
    version, algorithm, flags = ciphertext[0], ciphertext[1], ciphertext[2]
    if (version, algorithm) != (0x01, 0x01):
        raise DecryptError(f"unknown format version {version} / algorithm {algorithm}")
    if flags != 0:
        raise DecryptError(f"flags 0x{flags:02x} set; all flags are reserved in format v1")
    key_id = UUID(bytes=ciphertext[3:19])
    if aad_fields.get("key_id") != key_id:
        raise DecryptError("ciphertext key_id does not match the envelope's key_id")
    readable = conn.execute("SELECT %s = ANY (scopes.read_streams())", (aad_fields.get("stream_id"),)).fetchone()[0]
    if not readable:
        raise DecryptError("stream is not readable in this session")
    key = load_key(conn, provider, key_id)
    if key is None:
        return Shredded(key_id)
    aad = encode_envelope(aad_fields, Purpose.AAD) + ciphertext[:3]
    try:
        plaintext = AESGCM(key.material).decrypt(ciphertext[19:31], ciphertext[31:], aad)
    except InvalidTag:
        raise DecryptError("authentication failed: ciphertext or its envelope fields were altered") from None
    try:
        body = decode_cbor(plaintext)
    except CborDecodeError as exc:
        raise DecryptError(f"body is not canonical CBOR: {exc}") from None
    if type(body) is not dict:
        raise DecryptError("body is not a map")
    return body
