"""
Functionality: Make and parse Nacre bearer tokens, and compute their keyed hash.
Owns: the token string format (prefix, id, secret, CRC32 checksum), the token-hashing key file rules, and
  HMAC-SHA256(token key, secret), the only form in which a token is ever stored.
Public entry: new_token(), parse_token(), token_mac(), load_token_key(), TokenFormatError, PREFIX
Decisions: D-0026
Assumptions: A-0039
Notes: D-0026 §2 and amendment 2 (owner, 2026-10-02).
  - Format: `nacre_pat_<token_id: 32 hex>_<secret: 43 base62>_<crc32: 8 hex>`. The fixed prefix lets scanners
    recognise a leaked token; the CRC32 (over everything before it) rejects typos before any database lookup.
  - The secret is 256 bits from `secrets`. Only token_mac(secret) is stored, under a token-hashing key held OUTSIDE
    the database (a 32-byte file, mode 0600 or stricter), so a database copy alone cannot test guesses. It is separate
    from the root key, so root-key rotation never invalidates tokens.
  - Errors never contain the token or any part of the secret.
"""
import hashlib
import hmac
import os
import secrets
import stat
import string
import zlib
from pathlib import Path
from uuid import UUID

PREFIX = "nacre_pat_"
_B62 = string.digits + string.ascii_uppercase + string.ascii_lowercase
_SECRET_LEN = 43                       # ceil(256 / log2(62))


class TokenFormatError(ValueError):
    """The string is not a well-formed Nacre token (never echoes the input)."""


def _b62(n: int, width: int) -> str:
    out = []
    for _ in range(width):
        n, r = divmod(n, 62)
        out.append(_B62[r])
    return "".join(reversed(out))


def new_token() -> tuple[str, UUID, str]:
    """(token string shown once, token_id, secret)."""
    token_id = UUID(bytes=secrets.token_bytes(16))
    secret = _b62(secrets.randbits(256), _SECRET_LEN)
    body = f"{PREFIX}{token_id.hex}_{secret}"
    return f"{body}_{zlib.crc32(body.encode()):08x}", token_id, secret


def parse_token(token: str) -> tuple[UUID, str]:
    """(token_id, secret) of a well-formed token; TokenFormatError otherwise."""
    if not isinstance(token, str) or not token.startswith(PREFIX):
        raise TokenFormatError("not a Nacre token")
    parts = token[len(PREFIX):].split("_")
    if len(parts) != 3 or len(parts[0]) != 32 or len(parts[1]) != _SECRET_LEN or len(parts[2]) != 8:
        raise TokenFormatError("malformed Nacre token")
    tid, secret, crc = parts
    if any(c not in "0123456789abcdef" for c in tid + crc) or any(c not in _B62 for c in secret):
        raise TokenFormatError("malformed Nacre token")
    if f"{zlib.crc32(f'{PREFIX}{tid}_{secret}'.encode()):08x}" != crc:
        raise TokenFormatError("token checksum does not match (typo?)")
    return UUID(hex=tid), secret


def token_mac(key: bytes, secret: str) -> bytes:
    if len(key) != 32:
        raise TokenFormatError("token-hashing key must be 32 bytes")
    return hmac.new(key, b"nacre-token-v1|" + secret.encode(), hashlib.sha256).digest()


def load_token_key(path: Path | None = None) -> bytes:
    """The token-hashing key from `path` or NACRE_TOKEN_KEY_FILE; refuses a wrong size or a group/world-readable file."""
    p = Path(path or os.environ.get("NACRE_TOKEN_KEY_FILE", ""))
    if not p.is_file():
        raise TokenFormatError("token-hashing key file not found (set NACRE_TOKEN_KEY_FILE)")
    if p.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise TokenFormatError("token-hashing key file must not be readable by group or others (chmod 600)")
    key = p.read_bytes()
    if len(key) != 32:
        raise TokenFormatError("token-hashing key file must hold exactly 32 bytes")
    return key
