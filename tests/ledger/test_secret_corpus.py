"""Tests of the secret corpus itself (D-0007 amendments 2-4): determinism, the pinned digest, and that
every generator's output is a valid instance of its standard, checked by an independent parser."""
import base64
import hashlib
import json
import random
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest
from cryptography.hazmat.primitives import serialization

from secret_corpus import generic, synthetic_negatives  # noqa: F401  (registers generators)
from secret_corpus.corpus import GENERATORS, build, digest

# Pinned: changing any generator changes this. Update it deliberately, in the same commit.
CORPUS_SHA256 = "6a64f679983115dfdb5c7583be94a87587e1b9b47ea887e00e2858b9e4987632"


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
