"""
Functionality: Everything the write path does with a data key: encrypt an event body into the
  D-0008 ciphertext, count the key's uses, and derive the keyed MACs that die with the key.
Owns: body validation (D-0008 body map v1), the ciphertext header, the AAD, the per-key encryption
  count and its 2^28 cap, and HKDF sub-keys for request_mac / attachment_ref.
Public entry: encrypt_payload(), seal_bytes(), derive_mac()
Decisions: D-0002, D-0004, D-0007, D-0008
Assumptions: A-0015
Notes: Ciphertext = version(0x01) | algorithm(0x01, AES-256-GCM) | flags(0x00) | key_id(16) | nonce(12) | ct+tag.
  AAD = encode_envelope(AAD fields) | header bytes 0-2 (D-0008): the event's identity, type and
  payload type, plus the header's version, algorithm and flags, are all authenticated.
  The use count is incremented in the same transaction as the event insert. A rolled-back append
  rolls back its count too; its ciphertext was never stored, so the nonce was never exposed. (D1)
  MAC sub-keys: HKDF-SHA256(data key, info = "nacre-subkey-v1|" + purpose). They are shredded with
  the data key, so a MAC of a short plaintext cannot be brute-forced after erasure (D-0002).
"""
import hashlib
import hmac
import secrets
from collections.abc import Mapping
from enum import StrEnum

import psycopg
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from nacre.core.encode_cbor import encode_cbor
from nacre.keys.get_or_create_key import DataKey
from nacre.ledger.encode_envelope import Purpose, encode_envelope

FORMAT_VERSION, ALGORITHM_AES_256_GCM, FLAGS_V1 = 0x01, 0x01, 0x00
HEADER_BYTES = 3 + 16 + 12
_BODY_KEYS = {"content_version", "content", "person", "source_ref", "attachment", "redactions",
              "public_credentials"}  # D-0008 amendment 5: optional
_PERSON_KEYS = {"name", "handle", "email"}
_ATTACHMENT_KEYS = {"description", "media_type", "scan"}   # scan: D-0008 amendment 6


class MacPurpose(StrEnum):
    REQUEST_MAC = "request_mac"
    ATTACHMENT_REF = "attachment_ref"


class EncryptError(ValueError):
    """The body or key cannot be used to produce a valid ciphertext."""


class KeyExhausted(EncryptError):
    """The data key reached its 2^28 encryption cap (A-0015); a new key period is required."""


def encrypt_payload(conn: psycopg.Connection, key: DataKey, aad_fields: Mapping[str, object],
                    body: dict) -> bytes:
    """The D-0008 ciphertext of `body` under `key`, bound to the event's AAD fields."""
    if aad_fields.get("key_id") != key.key_id or aad_fields.get("stream_id") != key.stream_id:
        raise EncryptError("AAD key_id / stream_id do not match the data key")
    _validate_body(body)
    return seal_bytes(conn, key, encode_envelope(aad_fields, Purpose.AAD), encode_cbor(body))


def seal_bytes(conn: psycopg.Connection, key: DataKey, aad_prefix: bytes, plaintext: bytes) -> bytes:
    """The D-0008 ciphertext of raw bytes under `key` (bodies and attachments share it, D-0013).
    AAD = aad_prefix | header bytes 0-2. Counts the use against the key's 2^28 cap."""
    try:
        conn.execute("SAVEPOINT nacre_count")
        counted = conn.execute("UPDATE keys.data_keys SET encryption_count = encryption_count + 1 "
                               "WHERE key_id = %s RETURNING encryption_count", (key.key_id,)).fetchone()
        conn.execute("RELEASE SAVEPOINT nacre_count")
    except psycopg.errors.CheckViolation:
        conn.execute("ROLLBACK TO SAVEPOINT nacre_count")
        raise KeyExhausted(f"data key {key.key_id} reached its encryption cap") from None
    if counted is None:
        raise EncryptError(f"data key {key.key_id} is not writable here (shredded or not in the write set)")
    prefix = bytes([FORMAT_VERSION, ALGORITHM_AES_256_GCM, FLAGS_V1])
    nonce = secrets.token_bytes(12)
    return prefix + key.key_id.bytes + nonce + AESGCM(key.material).encrypt(nonce, plaintext, aad_prefix + prefix)


def derive_mac(key: DataKey, purpose: MacPurpose, data: bytes) -> bytes:
    """HMAC-SHA256 of `data` under the data key's sub-key for `purpose` (32 bytes)."""
    if not isinstance(purpose, MacPurpose):
        raise EncryptError(f"unknown MAC purpose {purpose!r}")
    subkey = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                  info=b"nacre-subkey-v1|" + purpose.value.encode()).derive(key.material)
    return hmac.new(subkey, data, hashlib.sha256).digest()


def _validate_body(body: object) -> None:
    if type(body) is not dict:
        raise EncryptError("body must be a dict")
    unknown = set(body) - _BODY_KEYS
    if unknown:
        raise EncryptError(f"unknown body keys {sorted(unknown)} (D-0008 body v1)")
    if type(body.get("content_version")) is not int or body["content_version"] < 1:
        raise EncryptError("content_version must be an int >= 1")
    if "content" not in body:
        raise EncryptError("content is required (null when an attachment carries it)")
    _check_map(body, "person", _PERSON_KEYS)
    _check_map(body, "attachment", _ATTACHMENT_KEYS)
    if "source_ref" in body and type(body["source_ref"]) is not str:
        raise EncryptError("source_ref must be text")
    for name in ("redactions", "public_credentials"):
        if name in body and (type(body[name]) is not list or any(type(r) is not str for r in body[name])):
            raise EncryptError(f"{name} must be a list of text")


def _check_map(body: dict, name: str, allowed: set[str]) -> None:
    if name not in body:
        return
    value = body[name]
    if type(value) is not dict or set(value) - allowed or any(type(v) is not str for v in value.values()):
        raise EncryptError(f"{name} must be a map of text with keys from {sorted(allowed)}")
