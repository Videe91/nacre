"""
Functionality: Resolve a bearer token to an authenticated principal, or refuse it.
Owns: the lookup by keyed hash (as nacre_auth), the expiry / revocation / disabled checks on every call, the
  constant-time comparison, the throttled last-used stamp, and the typed refusal codes.
Public entry: authenticate_principal(), Principal, AuthError, STAMP_INTERVAL
Decisions: D-0026
Assumptions: A-0039
Notes: D-0026 §3 and amendment 2 (owner, 2026-10-02).
  - The connection must be the `nacre_auth` role (DbRole.AUTH): it can read tokens and principals and stamp
    last_used_at, nothing else.
  - Refusals: `malformed` (format or checksum, before any lookup), `unknown`, `revoked`, `expired`, `disabled`. The
    error never contains the token.
  - Revocation and expiry are checked on EVERY call (immediate revocation): there is no cache.
  - last_used_at is written at most once per STAMP_INTERVAL per token (owner: throttled), so a busy agent does not
    turn every call into a write.
"""
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import psycopg

from nacre.interface.token_format import TokenFormatError, parse_token, token_mac

STAMP_INTERVAL = timedelta(minutes=10)


class AuthError(PermissionError):
    def __init__(self, code: str):
        super().__init__(f"unauthenticated: {code}")
        self.code = code


@dataclass(frozen=True)
class Principal:
    principal_id: UUID
    org_id: UUID
    kind: str                        # agent | person | service | operator
    service_sources: tuple[str, ...]
    token_id: UUID


def authenticate_principal(auth_conn: psycopg.Connection, token_key: bytes, token: str,
                           *, now: datetime | None = None) -> Principal:
    now = now or datetime.now(UTC)
    try:
        token_id, secret = parse_token(token)
    except TokenFormatError:
        raise AuthError("malformed") from None
    mac = token_mac(token_key, secret)
    row = auth_conn.execute(
        "SELECT t.token_id, t.token_mac, t.expires_at, t.revoked_at, p.principal_id, p.org_id, p.kind, "
        "p.service_sources, p.disabled_at, t.last_used_at FROM auth.tokens t "
        "JOIN auth.principals p ON p.principal_id = t.principal_id WHERE t.token_mac = %s", (mac,)).fetchone()
    if row is None or row[0] != token_id or not hmac.compare_digest(bytes(row[1]), mac):
        raise AuthError("unknown")
    _, _, expires_at, revoked_at, pid, org, kind, sources, disabled_at, last_used = row
    if revoked_at is not None:
        raise AuthError("revoked")
    if expires_at <= now:
        raise AuthError("expired")
    if disabled_at is not None:
        raise AuthError("disabled")
    if last_used is None or last_used < now - STAMP_INTERVAL:
        auth_conn.execute("UPDATE auth.tokens SET last_used_at = %s WHERE token_id = %s "
                          "AND (last_used_at IS NULL OR last_used_at < %s)", (now, token_id, now - STAMP_INTERVAL))
    auth_conn.commit()
    return Principal(pid, org, kind, tuple(sources), token_id)
