"""Tests for interface/token_format.py and interface/authenticate_principal.py (R2, D-0026 + amendment 2): format and
checksum, keyed-hash-only storage, every refusal code, immediate revocation, throttled stamping, no token in errors."""
import os
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from nacre.interface.authenticate_principal import STAMP_INTERVAL, AuthError, authenticate_principal
from nacre.interface.token_format import (TokenFormatError, load_token_key, new_token, parse_token, token_mac)

KEY = os.urandom(32)
NOW = datetime.now(UTC)


def _issue(admin, kind="agent", days=30, key=KEY):
    pid = uuid.uuid4()
    admin.execute("INSERT INTO auth.principals (principal_id, org_id, kind, display_name, config_event_id) "
                  "VALUES (%s, %s, %s, 'p', %s)", (pid, uuid.uuid4(), kind, uuid.uuid4()))
    token, tid, secret = new_token()
    admin.execute("INSERT INTO auth.tokens (token_id, principal_id, token_mac, expires_at) VALUES (%s, %s, %s, %s)",
                  (tid, pid, token_mac(key, secret), NOW + timedelta(days=days)))
    return token, tid, pid


@pytest.fixture
def admin(migrated_db):
    with psycopg.connect(migrated_db["principal_admin"], autocommit=True) as c:
        yield c


@pytest.fixture
def auth(migrated_db):
    with psycopg.connect(migrated_db["auth"]) as c:
        yield c


def test_tokens_round_trip_and_a_typo_fails_the_checksum():
    token, tid, secret = new_token()
    assert parse_token(token) == (tid, secret)
    i = len(token) - 12
    typo = token[:i] + ("A" if token[i] != "A" else "B") + token[i + 1:]
    with pytest.raises(TokenFormatError) as e:
        parse_token(typo)
    assert secret not in str(e.value) and token not in str(e.value)


def test_a_valid_token_authenticates_and_only_its_keyed_hash_is_stored(admin, auth, migrated_db):
    token, tid, pid = _issue(admin)
    p = authenticate_principal(auth, KEY, token, now=NOW)
    assert (p.principal_id, p.kind, p.token_id) == (pid, "agent", tid)
    with psycopg.connect(migrated_db["admin"]) as c:
        dump = b"".join(bytes(r[0]) for r in c.execute("SELECT token_mac FROM auth.tokens"))
    assert parse_token(token)[1].encode() not in dump


@pytest.mark.parametrize("case", ["revoked", "expired", "disabled", "unknown", "wrong_key", "malformed"])
def test_every_refusal_has_its_code_and_never_echoes_the_token(admin, auth, case):
    token, tid, pid = _issue(admin)
    now, key = NOW, KEY
    if case == "revoked":
        admin.execute("UPDATE auth.tokens SET revoked_at = now() WHERE token_id = %s", (tid,))
    elif case == "expired":
        now = NOW + timedelta(days=31)
    elif case == "disabled":
        admin.execute("UPDATE auth.principals SET disabled_at = now() WHERE principal_id = %s", (pid,))
    elif case == "unknown":
        token = new_token()[0]
    elif case == "wrong_key":
        key = os.urandom(32)
    elif case == "malformed":
        token = "nacre_pat_nope"
    with pytest.raises(AuthError) as e:
        authenticate_principal(auth, key, token, now=now)
    assert e.value.code == {"wrong_key": "unknown"}.get(case, case) and token not in str(e.value)


def test_revocation_takes_effect_on_the_very_next_call(admin, auth):
    token, tid, _ = _issue(admin)
    authenticate_principal(auth, KEY, token, now=NOW)
    admin.execute("UPDATE auth.tokens SET revoked_at = now() WHERE token_id = %s", (tid,))
    with pytest.raises(AuthError, match="revoked"):
        authenticate_principal(auth, KEY, token, now=NOW)


def test_last_used_is_stamped_at_most_once_per_interval(admin, auth, migrated_db):
    token, tid, _ = _issue(admin)
    def last_used():
        with psycopg.connect(migrated_db["admin"]) as c:
            return c.execute("SELECT last_used_at FROM auth.tokens WHERE token_id = %s", (tid,)).fetchone()[0]
    authenticate_principal(auth, KEY, token, now=NOW)
    first = last_used()
    authenticate_principal(auth, KEY, token, now=NOW + timedelta(minutes=1))
    assert last_used() == first                                         # throttled
    authenticate_principal(auth, KEY, token, now=NOW + STAMP_INTERVAL + timedelta(seconds=1))
    assert last_used() > first


def test_the_key_file_must_be_private_and_32_bytes(tmp_path):
    f = tmp_path / "token.key"
    f.write_bytes(os.urandom(32))
    os.chmod(f, 0o644)
    with pytest.raises(TokenFormatError, match="group or others"):
        load_token_key(f)
    os.chmod(f, 0o600)
    assert len(load_token_key(f)) == 32
    f.write_bytes(b"short")
    with pytest.raises(TokenFormatError, match="32 bytes"):
        load_token_key(f)
