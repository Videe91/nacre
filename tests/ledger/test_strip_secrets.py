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


# ---- A-0010 measurement (D-0007 amendments 3-5; D-0011 holdout) --------------------------------------
from secret_corpus.corpus import build_holdout  # noqa: E402
from secret_corpus.measure import overfitting_flags  # noqa: E402


@pytest.fixture(scope="module")
def working():
    return measure(build(), strip_secrets, split="working")


@pytest.fixture(scope="module")
def holdout():
    return measure(build_holdout(), strip_secrets, split="holdout")


def _secret_groups():
    return sorted(k for k in measure(build_holdout(per_generator=1), strip_secrets, split="holdout")["groups"]
                  if not k.startswith("public:"))


# Groups below 99% ON THE HOLDOUT (first holdout run, 2026-09-30). The vendored gitleaks `jwt` and
# `sentry-user-token` rules require a quote, whitespace or ';' after the token, so they miss the holdout's
# XML and error-message contexts. The working set showed 100%: this is the overfitting the holdout exists
# to catch. A fix would need new rules and a RE-SEALED holdout (owner decision, CURRENT.md).
# strict=True: when a group passes, this suite fails until the entry is removed.
HOLDOUT_GAPS = {
    "generic:jwt/hs256": "vendored `jwt` rule terminators miss XML/error-message contexts (holdout 78%)",
    "provider:Sentry": "vendored `sentry-user-token` terminators miss XML/error-message contexts (sntryu holdout 78%)",
    "provider:Supabase": "vendored `jwt` rule terminators: service_role JWT holdout 78%",
}


# Official (D-0011 amendment 1): catch rates come from the sealed holdout only.
@pytest.mark.parametrize("group", [
    pytest.param(g, marks=pytest.mark.xfail(strict=True, reason=HOLDOUT_GAPS[g])) if g in HOLDOUT_GAPS else g
    for g in _secret_groups()])
def test_catch_rate_holdout_official(holdout, group):
    assert holdout["groups"][group]["rate"] >= 0.99, holdout["groups"][group]


def test_public_credentials_are_never_stripped(working, holdout):
    for report in (working, holdout):
        public = {k: v for k, v in report["groups"].items() if k.startswith("public:")}
        assert public and all(v["rate"] == 1.0 for v in public.values()), public


def test_false_positive_rate_holdout_official(holdout):
    assert holdout["fp_rate"] <= 0.02, holdout["fp_docs"]


def test_working_set_reported_and_overfitting_flagged(working, holdout, capsys):
    flags = overfitting_flags(working, holdout)
    with capsys.disabled():
        print("\nA-0010 working vs holdout (overfitting flags: %s)" % (flags or "none"))
    assert working["fp_rate"] <= 0.02


def test_nacre_rules_cannot_reuse_a_gitleaks_rule_id(fresh_rules, tmp_path):
    # D-0011 amendment 2: additive only; a clash would shadow or override a vendored rule.
    clash = ss.NACRE_RULES_FILE.read_text().replace('id = "supabase-secret-key"', 'id = "github-pat"', 1).encode()
    copy = tmp_path / "nacre.toml"
    copy.write_bytes(clash)
    fresh_rules.setattr(ss, "NACRE_RULES_FILE", copy)
    fresh_rules.setattr(ss, "NACRE_RULES_SHA256", hashlib.sha256(clash).hexdigest())
    with pytest.raises(RulesError, match="additive only"):
        strip_secrets("x")


def test_a_modified_nacre_rules_file_is_refused(fresh_rules, tmp_path):
    copy = tmp_path / "nacre.toml"
    copy.write_bytes(ss.NACRE_RULES_FILE.read_bytes() + b"\n# loosened\n")
    fresh_rules.setattr(ss, "NACRE_RULES_FILE", copy)
    with pytest.raises(RulesError, match="pinned sha256"):
        strip_secrets("x")
