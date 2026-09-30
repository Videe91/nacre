"""
Provider generators whose formats are fully documented by the PROVIDER (docs, changelogs, or the
provider's own source code), verified 2026-09-30 (evidence/A-0010-format-verification.md). Written
independently of the gitleaks rules (D-0007 amendment 2). Known gaps are stated in each docstring.
Token types with any undocumented fact (charset, length, checksum) are NOT generated here: they wait for
owner-measured facts (scripts/measure_token_format.py) or stay unmeasured (D-0007 amendment 4).
"""
import base64
import hashlib
import hmac
import json
import uuid
import zlib

from secret_corpus.corpus import ALNUM, B64URL, HEX, generator, rand

_GH = ("https://github.blog/engineering/platform-security/behind-githubs-new-authentication-token-formats/ ; "
       "https://github.blog/changelog/2021-03-31-authentication-token-format-updates-are-generally-available/ ; "
       "https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/about-authentication-to-github")


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


# ---- GitHub -----------------------------------------------------------------------------------------
@generator(category="provider", provider="GitHub", kind="classic (ghp/gho/ghu/ghs/ghr)", source=_GH)
def github_classic(rng):
    """prefix + '_' + 30 [A-Za-z0-9] + 6-char checksum = 40. GAP: GitHub does not publish the CRC32 input
    bytes or its Base62 alphabet order, so the last 6 chars are random base62, not a valid checksum.
    Detection rules do not verify checksums, so catch rates are unaffected; recorded, not hidden."""
    return rng.choice(["ghp", "gho", "ghu", "ghs", "ghr"]) + "_" + rand(rng, ALNUM, 30) + rand(rng, ALNUM, 6)


@generator(category="provider", provider="GitHub", kind="ghs stateless installation token",
           source="https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/about-authentication-to-github ; "
                  "https://github.blog/changelog/2026-05-15-github-app-installation-tokens-per-request-override-header")
def github_ghs_stateless(rng):
    """'ghs_APPID_JWT', ~520 chars, two dots (GitHub, 2026). JWT claims are not published; a plausible RS256
    header and claim set are used, with a 2048-bit-sized signature."""
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
    iat = rng.randint(1_780_000_000, 1_790_000_000)
    claims = {"iat": iat, "exp": iat + 3600, "iss": str(rng.randint(100000, 9999999)),
              "installation_id": rng.randint(10_000_000, 99_999_999)}
    payload = _b64url(json.dumps(claims, separators=(",", ":")).encode())
    return f"ghs_{claims['iss']}_{header}.{payload}.{_b64url(rng.randbytes(256))}"


# ---- GitLab -----------------------------------------------------------------------------------------
_GITLAB_SRC = ("https://docs.gitlab.com/security/tokens/ ; gitlab-org/gitlab@18c8707f "
               "lib/authn/token_field/generator/routable_token.rb, app/models/personal_access_token.rb, "
               "lib/authn/token_field/base.rb")
_FRIENDLY = "".join(c for c in B64URL if c not in "lIO0")


@generator(category="provider", provider="GitLab", kind="glpat legacy", source=_GITLAB_SRC + " ; Devise.friendly_token")
def gitlab_legacy(rng):
    """glpat- + Devise.friendly_token (20 chars of urlsafe base64 with l/I/O/0 replaced by s/x/y/z).
    NOTE: Devise is a GitLab dependency, invoked by GitLab-owned code; the replacement skews the
    distribution, which is reproduced here."""
    raw = rand(rng, B64URL, 20)
    return "glpat-" + raw.translate(str.maketrans("lIO0", "sxyz"))


def _b36(n: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = ""
    while True:
        n, r = divmod(n, 36)
        out = digits[r] + out
        if n == 0:
            return out


@generator(category="provider", provider="GitLab", kind="glpat routable", source=_GITLAB_SRC)
def gitlab_routable(rng):
    """Exactly GitLab's RoutableToken: urlsafe-b64(16 random bytes + routing payload + 1-byte payload size),
    '.', version '01', '.', 2-char base36 b64 length, then 7-char base36 CRC32 of everything before it,
    prefix included. Routing payload for a PAT: sorted 'o:<org base36>\\nu:<user base36>' (cell id omitted,
    as compact_blank drops it when unset)."""
    payload = f"o:{_b36(rng.randint(1, 10**6))}\nu:{_b36(rng.randint(1, 10**8))}".encode()
    b64 = _b64url(rng.randbytes(16) + payload + bytes([len(payload)]))
    body = f"glpat-{b64}.{_b36(1).rjust(2, '0')}.{_b36(len(b64)).rjust(2, '0')}"
    return body + _b36(zlib.crc32(body.encode())).rjust(7, "0")


# ---- PyPI -------------------------------------------------------------------------------------------
def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b, n = n & 0x7F, n >> 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _field(t: int, data: bytes) -> bytes:
    return _varint(t) + _varint(len(data)) + data


@generator(category="provider", provider="PyPI", kind="API token (macaroon)",
           source="https://docs.pypi.org/api/secrets/ ; pypi/warehouse@8e4dc6d6 warehouse/macaroons/services.py L500-510 ; "
                  "libmacaroons v2 binary format (pymacaroons MACAROON_V2)")
def pypi_token(rng):
    """'pypi-' + urlsafe base64 (no padding) of a V2 binary macaroon: location = domain, identifier = UUID
    string, first-party caveats, HMAC-SHA256 signature chain (computed). Caveat JSON bodies are
    representative, not byte-exact copies of warehouse's current caveat encoding."""
    key = hmac.new(b"macaroons-key-generator", rng.randbytes(32), hashlib.sha256).digest()
    identifier = str(uuid.UUID(int=rng.getrandbits(128), version=4)).encode()
    caveats = [json.dumps({"version": 1, "permissions": rng.choice(["user", {"projects": [rand(rng, "abcdefghij", 8)]}])}).encode()]
    sig = hmac.new(key, identifier, hashlib.sha256).digest()
    blob = bytes([2]) + _field(1, b"pypi.org") + _field(2, identifier) + b"\x00"
    for c in caveats:
        blob += _field(2, c) + b"\x00"
        sig = hmac.new(sig, c, hashlib.sha256).digest()
    blob += b"\x00" + _field(6, sig)
    return "pypi-" + _b64url(blob)


# ---- Heroku -----------------------------------------------------------------------------------------
@generator(category="provider", provider="Heroku", kind="HRKU- UUID form",
           source="https://devcenter.heroku.com/changelog-items/2842 ; https://devcenter.heroku.com/changelog-items/2800")
def heroku_uuid(rng):
    """HRKU- + UUID (41 chars), the shape of Heroku's own changelog example. The current 65-char form's
    charset is not stated by Heroku: it waits for owner-measured facts."""
    return "HRKU-" + str(uuid.UUID(int=rng.getrandbits(128), version=4))


# ---- Supabase ---------------------------------------------------------------------------------------
_SUPA = ("https://supabase.com/docs/guides/api/api-keys ; supabase/supabase@ff80bb14 "
         "docker/utils/add-new-auth-keys.sh L125-135")


def _supabase_key(rng, prefix):
    """Supabase's own generator: prefix + 22 base64url + '_' + first 8 base64url chars of
    SHA-256(project_ref + '|' + prefix + random). PROJECT_REF as in Supabase's self-hosted script.
    GAP: hosted-platform keys are undocumented; assumed to share this shape."""
    random_part = _b64url(rng.randbytes(17))[:22]
    intermediate = prefix + random_part
    checksum = _b64url(hashlib.sha256(f"supabase-self-hosted|{intermediate}".encode()).digest())[:8]
    return f"{intermediate}_{checksum}"


@generator(category="provider", provider="Supabase", kind="sb_secret", source=_SUPA)
def supabase_secret(rng):
    return _supabase_key(rng, "sb_secret_")


@generator(category="provider", provider="Supabase", kind="sb_publishable", source=_SUPA, expected="public")
def supabase_publishable(rng):
    return _supabase_key(rng, "sb_publishable_")


def _supabase_jwt(rng, role):
    """Legacy anon / service_role keys: HS-signed JWTs told apart by the `role` claim (Supabase docs).
    GAP: the rest of the claim set is not documented; iss/iat/exp are representative."""
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    iat = rng.randint(1_650_000_000, 1_750_000_000)
    payload = _b64url(json.dumps({"iss": "supabase", "role": role, "iat": iat, "exp": iat + 10 * 365 * 86400},
                                 separators=(",", ":")).encode())
    sig = _b64url(hmac.new(rng.randbytes(32), f"{header}.{payload}".encode(), hashlib.sha256).digest())
    return f"{header}.{payload}.{sig}"


@generator(category="provider", provider="Supabase", kind="legacy service_role JWT", source=_SUPA)
def supabase_service_role(rng):
    return _supabase_jwt(rng, "service_role")


@generator(category="provider", provider="Supabase", kind="legacy anon JWT", source=_SUPA, expected="public")
def supabase_anon(rng):
    return _supabase_jwt(rng, "anon")


# ---- Sentry (getsentry/sentry@88dcaf3f) --------------------------------------------------------------
_SENTRY = "getsentry/sentry@88dcaf3f src/sentry/types/token.py, models/apitoken.py, utils/security/orgauthtoken_token.py"
_SENTRY_DSN = ("https://docs.sentry.io/concepts/key-terms/dsn-explainer/ ; getsentry/sentry@88dcaf3f "
               "src/sentry/models/projectkey.py (generate_api_key = token_hex(16); get_dsn)")


@generator(category="provider", provider="Sentry", kind="sntryu user token", source=_SENTRY)
def sentry_user(rng):
    """'sntryu_' + secrets.token_hex(32): 71 chars."""
    return "sntryu_" + rand(rng, HEX, 64)


@generator(category="provider", provider="Sentry", kind="sntrys org token", source=_SENTRY)
def sentry_org(rng):
    """'sntrys_' + standard base64 (padding kept) of JSON {iat, url, region_url, org} + '_' + 43-char
    standard base64 (padding stripped) of 32 random bytes."""
    org = rand(rng, "abcdefghijklmnopqrstuvwxyz", rng.randint(4, 12))
    payload = json.dumps({"iat": rng.randint(1_700_000_000, 1_790_000_000) + rng.random(),
                          "url": "https://sentry.io", "region_url": "https://us.sentry.io", "org": org})
    secret = base64.b64encode(rng.randbytes(32)).decode().rstrip("=")
    return f"sntrys_{base64.b64encode(payload.encode()).decode()}_{secret}"


def _dsn(rng, with_secret):
    host = f"o{rng.randint(100000, 9999999)}.ingest.sentry.io"
    key = rand(rng, HEX, 32) + (":" + rand(rng, HEX, 32) if with_secret else "")
    return f"https://{key}@{host}/{rng.randint(1000000, 9999999)}"


@generator(category="provider", provider="Sentry", kind="DSN (public key only)", source=_SENTRY_DSN, expected="public")
def sentry_dsn_public(rng):
    return _dsn(rng, with_secret=False)


@generator(category="provider", provider="Sentry", kind="legacy DSN with secret", source=_SENTRY_DSN)
def sentry_dsn_legacy(rng):
    return _dsn(rng, with_secret=True)


# The secret in a legacy DSN is its secret-key part; the public key and host stay.
sentry_dsn_legacy.secret_of = lambda dsn: dsn.split("://", 1)[1].split("@", 1)[0].split(":", 1)[1]
