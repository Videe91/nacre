"""
Generic-category generators (D-0007 amendment 3): private keys, DB connection strings with passwords,
credentials in URLs, JWTs, .env assignments. Formats come from standards, never from scanner rules.
Everything is deterministic from the seeded rng so the corpus sha256 is stable:
  - EC keys: ec.derive_private_key(seeded scalar); Ed25519: from seeded 32 bytes;
  - RSA: primes found by seeded Miller-Rabin; the key is assembled from its numbers;
  - OpenSSH: serialised by hand per PROTOCOL.key, because cryptography's OpenSSH writer draws a
    random checkint.
Encrypted PEM/OpenSSH variants are omitted: every standard encryption draws a random salt or IV.
"""
import base64
import hashlib
import hmac
import json
import struct
from urllib.parse import quote

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa

from secret_corpus.corpus import ALNUM, B64URL, generator, rand

PEM_STD = "RFC 7468 (PEM, 64-char lines); RFC 5208/5958 (PKCS#8)"
RSA_STD = "RFC 8017 App. A.1.2 (RSAPrivateKey); 'RSA PRIVATE KEY' label per OpenSSL PEM_read_bio_PrivateKey(3)"
EC_STD = "RFC 5915 §3-4 (ECPrivateKey, 'EC PRIVATE KEY'); SEC 1 v2 §C.4"
SSH_STD = "openssh-portable PROTOCOL.key; armor and 70-char wrap: sshkey.c, sshbuf-misc.c"


# ---- deterministic key material ------------------------------------------------------------------
_SMALL_PRIMES = [p for p in range(3, 2000, 2) if all(p % d for d in range(3, int(p ** 0.5) + 1, 2))]


def _is_probable_prime(n, rng, rounds=8):  # test data: 4^-8 error bound is ample
    if any(n % p == 0 for p in _SMALL_PRIMES):
        return n in _SMALL_PRIMES
    d, r = n - 1, 0
    while d % 2 == 0:
        d, r = d // 2, r + 1
    for _ in range(rounds):
        x = pow(rng.randrange(2, n - 2), d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _prime(rng, bits):
    while True:
        n = rng.getrandbits(bits) | (1 << (bits - 1)) | (1 << (bits - 2)) | 1
        if _is_probable_prime(n, rng):
            return n


def rsa_key(rng, bits=2048):
    e = 65537
    while True:
        p, q = _prime(rng, bits // 2), _prime(rng, bits // 2)
        phi = (p - 1) * (q - 1)
        if p != q and phi % e:
            break
    d = pow(e, -1, phi)
    numbers = rsa.RSAPrivateNumbers(p, q, d, d % (p - 1), d % (q - 1), pow(q, -1, p), rsa.RSAPublicNumbers(e, p * q))
    return numbers.private_key()


def ec_key(rng, curve):
    return ec.derive_private_key(rng.randrange(1, 2 ** (curve.key_size - 1)), curve)


def _pem(key, fmt):
    return key.private_bytes(serialization.Encoding.PEM, fmt, serialization.NoEncryption()).decode()


# ---- PEM private keys ------------------------------------------------------------------------------
@generator(category="generic", provider="private-key", kind="pkcs8-rsa", count=6, source=PEM_STD, embed=False)
def pkcs8_rsa(rng):
    return _pem(rsa_key(rng), serialization.PrivateFormat.PKCS8)


@generator(category="generic", provider="private-key", kind="pkcs8-ec", source=PEM_STD, embed=False)
def pkcs8_ec(rng):
    return _pem(ec_key(rng, rng.choice([ec.SECP256R1(), ec.SECP384R1()])), serialization.PrivateFormat.PKCS8)


@generator(category="generic", provider="private-key", kind="pkcs1-rsa", count=6, source=RSA_STD, embed=False)
def pkcs1_rsa(rng):
    return _pem(rsa_key(rng), serialization.PrivateFormat.TraditionalOpenSSL)


@generator(category="generic", provider="private-key", kind="sec1-ec", source=EC_STD, embed=False)
def sec1_ec(rng):
    return _pem(ec_key(rng, rng.choice([ec.SECP256R1(), ec.SECP384R1()])), serialization.PrivateFormat.TraditionalOpenSSL)


# ---- OpenSSH private keys (PROTOCOL.key, unencrypted: cipher "none", kdf "none") ------------------
def _s(b: bytes) -> bytes:
    return struct.pack(">I", len(b)) + b


def _mpint(n: int) -> bytes:
    raw = n.to_bytes((n.bit_length() + 8) // 8, "big") if n else b""
    return _s(raw)


def _openssh(rng, key_type: bytes, public_blob: bytes, private_fields: bytes) -> str:
    check = rng.getrandbits(32)
    body = struct.pack(">II", check, check) + _s(key_type) + private_fields + _s(b"nacre-corpus")
    pad = 1
    while len(body) % 8:
        body += bytes([pad])
        pad += 1
    blob = (b"openssh-key-v1\x00" + _s(b"none") + _s(b"none") + _s(b"") + struct.pack(">I", 1)
            + _s(public_blob) + _s(body))
    b64 = base64.b64encode(blob).decode()
    lines = [b64[i:i + 70] for i in range(0, len(b64), 70)]
    return "-----BEGIN OPENSSH PRIVATE KEY-----\n" + "\n".join(lines) + "\n-----END OPENSSH PRIVATE KEY-----\n"


@generator(category="generic", provider="private-key", kind="openssh-ed25519", source=SSH_STD, embed=False)
def openssh_ed25519(rng):
    sk = ed25519.Ed25519PrivateKey.from_private_bytes(rng.randbytes(32))
    seed = sk.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    pk = sk.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    public_blob = _s(b"ssh-ed25519") + _s(pk)
    return _openssh(rng, b"ssh-ed25519", public_blob, _s(pk) + _s(seed + pk))


@generator(category="generic", provider="private-key", kind="openssh-rsa", count=6, source=SSH_STD, embed=False)
def openssh_rsa(rng):
    n_ = rsa_key(rng).private_numbers()
    pub = n_.public_numbers
    public_blob = _s(b"ssh-rsa") + _mpint(pub.e) + _mpint(pub.n)
    private = _mpint(pub.n) + _mpint(pub.e) + _mpint(n_.d) + _mpint(n_.iqmp) + _mpint(n_.p) + _mpint(n_.q)
    return _openssh(rng, b"ssh-rsa", public_blob, private)


# ---- DB connection strings and credentials in URLs ------------------------------------------------
_PW_ALPHABET = ALNUM + "!$%&*+-.=?@^_~"


def _password(rng):
    return quote(rand(rng, _PW_ALPHABET, rng.randint(12, 32)), safe="")


def _uri_secret(uri: str) -> str:
    return uri.split("://", 1)[1].split("@", 1)[0].split(":", 1)[1]


def _user(rng):
    return rng.choice(["app", "admin", "svc_orders", "reporting", "deploy", "etl"])


def _host(rng):
    return rng.choice(["db.internal", "10.0.3.17", "orders-prod.cluster-x.eu-west-1.rds.example.com", "localhost"])


@generator(category="generic", provider="db-connection-string", kind="postgresql",
           source="PostgreSQL libpq §32.1.1.2 (connection URIs)")
def pg_uri(rng):
    scheme = rng.choice(["postgresql", "postgres"])
    return f"{scheme}://{_user(rng)}:{_password(rng)}@{_host(rng)}:5432/{rng.choice(['orders', 'app', 'analytics'])}?sslmode=require"


@generator(category="generic", provider="db-connection-string", kind="mysql",
           source="MySQL 8.4 Reference Manual: connecting using URI-like strings")
def mysql_uri(rng):
    return f"{rng.choice(['mysql', 'mysqlx'])}://{_user(rng)}:{_password(rng)}@{_host(rng)}:3306/{rng.choice(['shop', 'app'])}"


@generator(category="generic", provider="db-connection-string", kind="mongodb",
           source="MongoDB manual: connection string formats (standard and SRV)")
def mongo_uri(rng):
    if rng.random() < 0.5:
        return f"mongodb+srv://{_user(rng)}:{_password(rng)}@cluster0.abcde.mongodb.net/app?retryWrites=true&w=majority"
    return f"mongodb://{_user(rng)}:{_password(rng)}@{_host(rng)}:27017,{_host(rng)}:27017/admin?replicaSet=rs0"


@generator(category="generic", provider="credentials-in-url", kind="https-userinfo",
           source="RFC 3986 §3.2.1 (userinfo)")
def url_creds(rng):
    host = rng.choice(["git.example.com", "registry.example.org", "api.partner.example.net"])
    return f"https://{_user(rng)}:{_password(rng)}@{host}/{rng.choice(['repo.git', 'v2/', 'export?format=csv'])}"


for _make in (pg_uri, mysql_uri, mongo_uri, url_creds):
    _make.secret_of = _uri_secret


# ---- JWTs (RFC 7515 §7.1 compact serialization, RFC 7519) -----------------------------------------
def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


@generator(category="generic", provider="jwt", kind="hs256", source="RFC 7515 §7.1; RFC 7519 §3")
def jwt_hs256(rng):
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    iat = rng.randint(1_700_000_000, 1_790_000_000)
    claims = {"sub": rand(rng, ALNUM, 12), "iat": iat, "exp": iat + 3600,
              "scope": rng.choice(["read", "read write", "admin"])}
    payload = _b64url(json.dumps(claims, separators=(",", ":")).encode())
    signature = _b64url(hmac.new(rng.randbytes(32), f"{header}.{payload}".encode(), hashlib.sha256).digest())
    return f"{header}.{payload}.{signature}"


# ---- .env assignments (dialect: Docker Compose ".env file syntax"; D1 choice, see CURRENT.md) --------
_ENV_NAMES = ["API_KEY", "SECRET_KEY", "DB_PASSWORD", "AUTH_TOKEN", "CLIENT_SECRET", "PRIVATE_TOKEN",
              "ACCESS_TOKEN", "SIGNING_SECRET", "SMTP_PASSWORD", "WEBHOOK_SECRET"]


@generator(category="generic", provider="dotenv", kind="assignment", embed=False,
           source="Docker Compose docs: .env file syntax (KEY=VAL, quoting rules, # comments)")
def dotenv(rng):
    value = rand(rng, B64URL, rng.randint(24, 48))
    name = rng.choice(_ENV_NAMES)
    line = rng.choice([f"{name}={value}", f'{name}="{value}"', f"{name}='{value}'", f"export {name}={value}"])
    filler = ["# service configuration", "DEBUG=false", "PORT=8080", "LOG_LEVEL=info", "REGION=eu-west-1"]
    lines = rng.sample(filler, 3)
    lines.insert(rng.randint(0, 3), line)
    return "\n".join(lines) + "\n"


def _dotenv_secret(document: str) -> str:
    for line in document.splitlines():
        name, _, value = line.removeprefix("export ").partition("=")
        if name in _ENV_NAMES:
            return value.strip("'\"")
    raise ValueError("no secret assignment in document")


dotenv.secret_of = _dotenv_secret
