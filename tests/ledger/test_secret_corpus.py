"""Tests of the secret corpus itself (D-0007 amendments 2-4): determinism, the pinned digest, and that
every generator's output is a valid instance of its standard, checked by an independent parser."""
import base64
import hashlib
import json
import random
import re
import zlib
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest
from cryptography.hazmat.primitives import serialization

from secret_corpus import generic, providers, synthetic_negatives  # noqa: F401  (registers generators)
from secret_corpus.corpus import CONTEXTS, GENERATORS, HOLDOUT_CONTEXTS, HOLDOUT_SEED, build, build_holdout, digest

# Pinned: changing any generator changes this. Update it deliberately, in the same commit.
CORPUS_SHA256 = "686f675007f4f7920f22f5c335e91841b35aac5679967d2b171f06a2ce64b175"


@pytest.fixture(scope="module")
def corpus():
    return build()


def _outputs(name, n=5):
    rng = random.Random(f"structure|{name}")
    return [GENERATORS[name].make(rng) for _ in range(n)]


def _names(prefix):
    return [n for n in sorted(GENERATORS) if n.startswith(prefix)]


def test_corpus_is_pinned(corpus):
    # A non-deterministic generator would fail this pin on every run, so it also guards determinism.
    assert digest(corpus) == CORPUS_SHA256


# Sealed holdout (D-0011 amendment 1): pinned in the commit BEFORE any Nacre rule was written.
HOLDOUT_SHA256 = "e138000609a639e7456c0adba676a49bf17a111af02d64c94ed01b578feb2345"


def test_holdout_is_pinned_and_distinct_from_working():
    assert digest(build_holdout()) == HOLDOUT_SHA256
    assert HOLDOUT_SEED != 20260930
    probe = "PROBE-VALUE-123"
    assert not {c(probe) for c in CONTEXTS} & {c(probe) for c in HOLDOUT_CONTEXTS}


def test_every_secret_occurs_in_its_text(corpus):
    for s in corpus:
        if s.expected in ("secret", "public"):
            assert s.secret and s.secret in s.text, s.kind


def test_every_generator_cites_a_source():
    for g in GENERATORS.values():
        assert g.source.strip(), g.name
        assert "gitleaks" not in g.source.lower()   # independence rule (D-0007 amendment 2)


@pytest.mark.parametrize("name", _names("generic/private-key/pkcs") + _names("generic/private-key/sec1"))
def test_pem_keys_load(name):
    for pem in _outputs(name, 2):
        assert serialization.load_pem_private_key(pem.encode(), password=None) is not None
        body = pem.strip().splitlines()[1:-1]
        assert all(len(line) == 64 for line in body[:-1]) and 0 < len(body[-1]) <= 64   # RFC 7468


@pytest.mark.parametrize("name", _names("generic/private-key/openssh"))
def test_openssh_keys_load(name):
    for key in _outputs(name, 2):
        assert serialization.load_ssh_private_key(key.encode(), password=None) is not None
        body = key.strip().splitlines()[1:-1]
        assert all(len(line) == 70 for line in body[:-1])


@pytest.mark.parametrize("name", _names("generic/db-connection-string") + _names("generic/credentials-in-url"))
def test_uris_carry_a_password(name):
    for uri in _outputs(name):
        parts = urlsplit(uri if "," not in uri.split("@", 1)[1].split("/")[0] else uri.replace(",", "", 1).split(",")[0])
        assert parts.password, uri.split("@")[0].split(":")[0]
        secret = GENERATORS[name].make.secret_of(uri)
        assert unquote(secret) == unquote(parts.password)


def test_jwts_are_compact_jws():
    for token in _outputs("generic/jwt/hs256"):
        header, payload, signature = token.split(".")
        pad = lambda s: s + "=" * (-len(s) % 4)  # noqa: E731
        assert json.loads(base64.urlsafe_b64decode(pad(header)))["alg"] == "HS256"
        assert "exp" in json.loads(base64.urlsafe_b64decode(pad(payload)))
        assert len(base64.urlsafe_b64decode(pad(signature))) == 32
        assert "=" not in token


def test_dotenv_documents_hold_exactly_one_secret_assignment():
    for doc in _outputs("generic/dotenv/assignment"):
        secret = GENERATORS["generic/dotenv/assignment"].make.secret_of(doc)
        assert len(secret) >= 24 and doc.count(secret) == 1


CORPUS_DIR = Path(__file__).resolve().parent / "secret_corpus"
PERMISSIVE = {"MIT", "MIT-0", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0", "PSF-2.0",
              "Apache-2.0 OR BSD-2-Clause", "Apache-2.0 OR BSD-3-Clause"}


@pytest.mark.parametrize("sub", ["negatives", "negatives_external"])
def test_committed_negatives_match_their_manifest(sub):
    manifest = json.loads((CORPUS_DIR / sub / "MANIFEST.json").read_text())
    on_disk = {p.relative_to(CORPUS_DIR / sub).as_posix() for p in (CORPUS_DIR / sub).rglob("*")
               if p.is_file() and p.name != "MANIFEST.json" and "LICENSE" not in p.name.upper()
               and "/LICENSES/" not in p.as_posix()}
    assert on_disk == {f["path"] for f in manifest["files"]}, "unlisted or missing negative files"
    for f in manifest["files"]:
        assert hashlib.sha256((CORPUS_DIR / sub / f["path"]).read_bytes()).hexdigest() == f["sha256"], f["path"]


def test_negatives_are_permissive_attributed_and_reviewed():
    manifest = json.loads((CORPUS_DIR / "negatives/MANIFEST.json").read_text())
    for name, pkg in manifest["packages"].items():
        assert pkg["licence"] in PERMISSIVE, name
    used = {f["path"].split("/", 1)[0] for f in manifest["files"]}
    for name in used:
        files = manifest["packages"][name]["licence_files"]
        assert files and all((CORPUS_DIR / "negatives" / lf).is_file() for lf in files), f"{name}: no licence file"
    flagged = {f["path"] for f in manifest["files"] if f["prescan_findings"]}
    assert flagged <= set(manifest["reviewed_false_positives"]), "unreviewed pre-scan findings"
    assert not any(p.startswith(("hypothesis/", "psycopg")) for p in (f["path"] for f in manifest["files"]))
    assert not any("pip/_vendor/" in f["path"] for f in manifest["files"])


def test_synthetic_negatives_are_negative(corpus):
    negs = [s for s in corpus if s.category == "negative"]
    assert negs and all(s.expected == "negative" and s.secret is None for s in negs)


# ---- provider generators, checked against each provider's own rules --------------------------------
def _b64url_dec(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def test_github_classic_shape():
    for tok in _outputs("provider/GitHub/classic (ghp/gho/ghu/ghs/ghr)", 20):
        assert re.fullmatch(r"gh[pousr]_[A-Za-z0-9]{36}", tok) and len(tok) == 40


def test_github_stateless_ghs_matches_githubs_published_regex():
    for tok in _outputs("provider/GitHub/ghs stateless installation token"):
        assert re.fullmatch(r"ghs_[A-Za-z0-9\.\-_]{36,}", tok)          # GitHub changelog 2026-05-15
        assert tok.count(".") == 2 and 450 <= len(tok) <= 600


def test_gitlab_legacy_uses_friendly_token_alphabet():
    for tok in _outputs("provider/GitLab/glpat legacy", 20):
        body = tok.removeprefix("glpat-")
        assert len(body) == 20 and not set(body) & set("lIO0")


def test_gitlab_routable_crc_and_payload_as_gitlab_computes_them():
    for tok in _outputs("provider/GitLab/glpat routable", 20):
        body, crc = tok[:-7], tok[-7:]
        assert int(crc, 36) == zlib.crc32(body.encode())
        prefix_b64, version, length = body.split(".")
        b64 = prefix_b64.removeprefix("glpat-")
        assert version == "01" and int(length, 36) == len(b64)
        raw = _b64url_dec(b64)
        payload = raw[16:-1]
        assert raw[-1] == len(payload) and re.fullmatch(rb"o:[0-9a-z]+\nu:[0-9a-z]+", payload)


def test_pypi_matches_pypis_published_pattern_and_is_a_v2_macaroon():
    for tok in _outputs("provider/PyPI/API token (macaroon)"):
        assert re.fullmatch(r"pypi-[A-Za-z0-9-_]{85,}", tok)            # docs.pypi.org/api/secrets
        raw = _b64url_dec(tok.removeprefix("pypi-"))
        assert raw[0] == 2 and b"pypi.org" in raw[:16]


def test_heroku_uuid_form():
    for tok in _outputs("provider/Heroku/HRKU- UUID form"):
        assert len(tok) == 41 and re.fullmatch(r"HRKU-[0-9a-f-]{36}", tok)


@pytest.mark.parametrize("name,prefix,length", [("provider/Supabase/sb_secret", "sb_secret_", 41),
                                                ("provider/Supabase/sb_publishable", "sb_publishable_", 46)])
def test_supabase_keys_checksum_as_supabase_computes_it(name, prefix, length):
    for tok in _outputs(name):
        # Positional: the base64url checksum may itself contain '_', so never split on it.
        intermediate, sep, checksum = tok[:-9], tok[-9], tok[-8:]
        assert len(tok) == length and sep == "_" and intermediate.startswith(prefix)
        expected = base64.urlsafe_b64encode(hashlib.sha256(f"supabase-self-hosted|{intermediate}".encode()).digest())
        assert checksum == expected.decode()[:8]


@pytest.mark.parametrize("name,role", [("provider/Supabase/legacy service_role JWT", "service_role"),
                                       ("provider/Supabase/legacy anon JWT", "anon")])
def test_supabase_legacy_jwts_differ_only_by_role(name, role):
    for tok in _outputs(name):
        assert json.loads(_b64url_dec(tok.split(".")[1]))["role"] == role


def test_supabase_look_alikes_are_classified_by_role():
    assert GENERATORS["provider/Supabase/legacy service_role JWT"].expected == "secret"
    assert GENERATORS["provider/Supabase/legacy anon JWT"].expected == "public"
    assert GENERATORS["provider/Supabase/sb_publishable"].expected == "public"


def test_sentry_tokens_parse_like_sentry():
    for tok in _outputs("provider/Sentry/sntryu user token"):
        assert re.fullmatch(r"sntryu_[0-9a-f]{64}", tok)
    for tok in _outputs("provider/Sentry/sntrys org token"):
        assert tok.count("_") == 2                                      # Sentry's own parser check
        _, payload, secret = tok.split("_")
        assert set(json.loads(base64.b64decode(payload))) == {"iat", "url", "region_url", "org"}
        assert len(secret) == 43


def test_sentry_dsns_public_vs_legacy():
    for dsn in _outputs("provider/Sentry/DSN (public key only)"):
        parts = urlsplit(dsn)
        assert re.fullmatch(r"[0-9a-f]{32}", parts.username) and parts.password is None
    for dsn in _outputs("provider/Sentry/legacy DSN with secret"):
        parts = urlsplit(dsn)
        assert re.fullmatch(r"[0-9a-f]{32}", parts.password)
        assert GENERATORS["provider/Sentry/legacy DSN with secret"].make.secret_of(dsn) == parts.password
