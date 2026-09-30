"""Tests for ledger/strip_secrets.py (D-0007, D-0009): behaviour, rule pinning, and the A-0010 measurement."""
import base64
import hashlib
import hmac
import json
import random
import string

import pytest

from nacre.ledger import strip_secrets as ss
from nacre.ledger.strip_secrets import RulesError, strip_secrets
from secret_corpus import generic, providers, synthetic_negatives  # noqa: F401
from secret_corpus.corpus import build
from secret_corpus.measure import measure

rng = random.Random(99)


def _gh():  # built at runtime so no secret-shaped literal is committed (D-0010)
    return "gh" + "p_" + "".join(rng.choice(string.ascii_letters + string.digits) for _ in range(36))


def _jwt(role):
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()  # noqa: E731
    head, body = enc({"alg": "HS256", "typ": "JWT"}), enc({"iss": "supabase", "role": role, "iat": 1})
    sig = base64.urlsafe_b64encode(hmac.new(b"k" * 32, f"{head}.{body}".encode(), hashlib.sha256).digest()).rstrip(b"=")
    return f"{head}.{body}.{sig.decode()}"


# ---- behaviour ---------------------------------------------------------------------------------------
def test_only_the_secret_part_is_replaced():
    token = _gh()
    r = strip_secrets(f'token = "{token}"\n')
    assert token not in r.text and r.text.startswith('token = "[REDACTED:') and r.text.endswith('"\n')
    assert r.redactions and r.findings[0].rule_id == r.redactions[0]


def test_clean_text_is_untouched():
    text = "def add(a, b):\n    return a + b\n"
    r = strip_secrets(text)
    assert (r.text, r.redactions, r.public_credentials, r.findings) == (text, (), (), ())


def test_idempotent():
    once = strip_secrets(f"a={_gh()}\nb={_gh()}\n").text
    assert strip_secrets(once).text == once


def test_findings_are_offsets_in_the_original_text():
    token = _gh()
    text = f"x\ny = '{token}'\n"
    (f,) = strip_secrets(text).findings
    assert text[f.start:f.end] == token


def test_public_credentials_are_kept_and_reported():
    anon = _jwt("anon")
    dsn = "https://" + "a" * 32 + "@o1.ingest.sentry.io/42"
    r = strip_secrets(f"SUPABASE_ANON={anon}\nSENTRY_DSN={dsn}\n")
    assert anon in r.text and dsn in r.text
    assert r.public_credentials == ("sentry-dsn-public", "supabase-anon-jwt")


def test_service_role_look_alike_is_stripped():
    service = _jwt("service_role")
    r = strip_secrets(f"SUPABASE_SERVICE={service}\n")
    assert service not in r.text and r.public_credentials == ()


def test_entropy_layer_skips_code_expressions_but_catches_quoted_values():
    assert strip_secrets("OID = SignatureAlgorithmOID.RSA_WITH_SHA256\n").findings == ()
    value = "".join(rng.choice(string.ascii_letters + string.digits) for _ in range(32)) + "aZ9"
    assert value not in strip_secrets(f'x = "{value}"\n').text


def test_entropy_layer_ignores_long_blobs_and_url_tails():
    blob = base64.b64encode(bytes(range(256)) * 2).decode()
    assert strip_secrets(f'"content": "{blob}"\n').findings == ()
    assert strip_secrets("# see https://github.com/python/cpython/blob/8c21941ddaf/Lib/x.py\n").findings == ()


# ---- rule pinning (D-0007, D-0009) ---------------------------------------------------------------------
@pytest.fixture
def fresh_rules(monkeypatch):
    ss._rules.cache_clear()
    yield monkeypatch
    ss._rules.cache_clear()


def test_a_modified_rules_file_is_refused(fresh_rules, tmp_path):
    copy = tmp_path / "rules.toml"
    copy.write_bytes(ss.RULES_FILE.read_bytes() + b"\n# edited\n")
    fresh_rules.setattr(ss, "RULES_FILE", copy)
    with pytest.raises(RulesError, match="pinned sha256"):
        strip_secrets("x")


def test_a_rule_that_does_not_compile_fails_the_load(fresh_rules, tmp_path):
    bad = ss.RULES_FILE.read_text().replace('regex = ', 'regex = """(?<=lookbehind)""" #', 1).encode()
    copy = tmp_path / "rules.toml"
    copy.write_bytes(bad)
    fresh_rules.setattr(ss, "RULES_FILE", copy)
    fresh_rules.setattr(ss, "RULES_SHA256", hashlib.sha256(bad).hexdigest())
    with pytest.raises(RulesError, match="failed to compile"):
        strip_secrets("x")


# ---- A-0010 measurement (D-0007 amendments 3-5) ------------------------------------------------------
@pytest.fixture(scope="module")
def report():
    return measure(build(), strip_secrets)


# Groups below 99% on 2026-09-30, each explained by a gap proposed to close in D-0011 (supplementary
# rules). strict=True: a group that starts passing fails this suite until it is removed from the list.
KNOWN_GAPS = {
    "generic:credentials-in-url/https-userinfo": "no gitleaks rule for URL userinfo passwords (D-0011)",
    "generic:db-connection-string/mongodb": "no gitleaks rule for URL userinfo passwords (D-0011)",
    "generic:db-connection-string/mysql": "no gitleaks rule for URL userinfo passwords (D-0011)",
    "generic:db-connection-string/postgresql": "no gitleaks rule for URL userinfo passwords (D-0011)",
    "provider:GitHub": "stateless ghs_ (2026) not in gitleaks v8.30.1 (D-0011)",
    "provider:Heroku": "gitleaks HRKU- rule needs a keyword context (D-0011)",
    "provider:Sentry": "legacy DSN secret (URL userinfo) and sntrys_ outside Bearer contexts (D-0011)",
    "provider:Supabase": "sb_secret_ outside Bearer contexts (D-0011)",
}
def _secret_groups():
    return sorted(k for k in measure(build(per_generator=1), lambda t: strip_secrets(t))["groups"] if not k.startswith("public:"))


@pytest.mark.parametrize("group", [
    pytest.param(g, marks=pytest.mark.xfail(strict=True, reason=KNOWN_GAPS[g])) if g in KNOWN_GAPS else g
    for g in _secret_groups()])
def test_catch_rate_at_least_99_percent_per_group(report, group):
    assert report["groups"][group]["rate"] >= 0.99, report["groups"][group]


def test_public_credentials_are_never_stripped(report):
    public = {k: v for k, v in report["groups"].items() if k.startswith("public:")}
    assert public and all(v["rate"] == 1.0 for v in public.values()), public


def test_false_positive_rate_at_most_2_percent(report):
    assert report["fp_rate"] <= 0.02, report["fp_docs"]
