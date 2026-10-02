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


# ---- A-0010 measurement (D-0007 amendments 3-5; D-0011 amendments 1, 5, 8) --------------------------
# Official: sealed holdout H3 (holdout log). Caught = every character of the secret part redacted.
# Slow: `pytest -m holdout` (the pre-commit hook runs it when detector, rules or corpus change).
from secret_corpus import credential_slot  # noqa: E402,F401
from secret_corpus.corpus import build_working  # noqa: E402
from secret_corpus.measure import overfitting_flags  # noqa: E402


@pytest.fixture(scope="module")
def working():
    return measure(build_working(), strip_secrets, split="all")


@pytest.fixture(scope="module")
def official():
    from secret_corpus import holdout_3
    return measure(holdout_3.build_holdout3(), strip_secrets, split="holdout3")


def _gated_groups():
    from secret_corpus import holdout_3
    groups = measure(holdout_3.build_holdout3(per_generator=1), strip_secrets, split="holdout3")["groups"]
    return sorted(k for k in groups if k.startswith(("provider:", "generic:")))


# Groups below 99% on H3. Filled only from an H3 measurement, never by guess; strict xfail if any.
HOLDOUT_GAPS: dict[str, str] = {}


@pytest.mark.holdout
@pytest.mark.parametrize("group", [
    pytest.param(g, marks=pytest.mark.xfail(strict=True, reason=HOLDOUT_GAPS[g])) if g in HOLDOUT_GAPS else g
    for g in _gated_groups()])
def test_caught_per_character_official_h3(official, group):
    g = official["groups"][group]
    assert g["strict_rate"] >= 0.99, g


@pytest.mark.holdout
def test_public_credentials_are_never_stripped(working, official):
    for report in (working, official):
        public = {k: v for k, v in report["groups"].items() if k.startswith("public:")}
        assert public and all(v["rate"] == 1.0 for v in public.values()), public


@pytest.mark.holdout
def test_false_positive_rate_official_h3(official):
    assert official["fp_rate"] <= 0.02, official["fp_docs"]


@pytest.mark.holdout
def test_working_reported_and_overfitting_flagged(working, official, capsys):
    flags = overfitting_flags(working, official)
    slots = {k: round(v["strict_rate"], 3) for k, v in official["groups"].items() if k.startswith("credential-slot:")}
    with capsys.disabled():
        print(f"\nA-0010 H3 overfitting flags: {flags or 'none'}; credential-slot strip rates (reported): {slots}")
    assert working["fp_rate"] <= 0.02


# ---- credential-slot-value (D-0011 amendment 8; Nacre rule, 2026-10-02) ----------------------------------------
def _slot_value(seed):
    import random
    r = random.Random(seed)
    return "".join(r.choice("abcdef0123456789") for _ in range(40))      # random hex, built at runtime


@pytest.mark.parametrize("shape", [
    'db_password: >-\n  {v}\n',                                    # YAML folded block scalar
    'let secret = String::from("{v}");',                          # call wrapper
    'proxy_set_header X-Auth-Token {v};',                         # space-separated header
    '<config><credential>{v}</credential></config>',              # XML element
    "INSERT INTO settings (k, v) VALUES ('cred', '{v}');",        # quoted key-value pair
    'machine example.test login ci password {v}',                 # netrc, last bytes
    "headers = {{'Authorization': 'Token {v}'}}",                 # auth scheme word
    'spring.datasource.password={v}\nspring.datasource.url=x',   # dotted key, unquoted
    'docker login --username ci --password {v} registry.test',   # CLI flag
])
def test_credential_slot_rule_shapes(shape):
    v = _slot_value(shape)
    r = strip_secrets(shape.format(v=v))
    assert v not in r.text and "credential-slot-value" in {f.rule_id for f in r.findings} | set(r.redactions)


@pytest.mark.parametrize("text", [
    "password_length = 1234567890123456",                         # key suffix not a credential slot
    "token = tokenize.generate_tokens(readline)",                 # code expression
    "self.secret = secret",
    "author: Jane Doe 2026",                                      # 'auth' inside another word
    "password: correcthorsebatterystaple",                        # no digit: not taken (documented trade-off)
    "api_key: ${API_KEY_2026}",                                   # placeholder
    "secret: 0000000000000000",                                   # entropy <= 3.0
    "credential_type: OAuthClientCredentials2",                   # suffix `_type` is not a slot
])
def test_credential_slot_rule_leaves_non_values_alone(text):
    assert "credential-slot-value" not in {f.rule_id for f in strip_secrets(text).findings}


# Multi-line string literals in a credential slot, one per documented form (scripts/build_credential_slot_regex.py
# lists the language references). The value starts on a later line than the key.
_Q3, _S3 = '"' * 3, "'" * 3


@pytest.mark.parametrize("shape", [
    "password = " + _Q3 + "\n    {v}\n" + _Q3,                               # Python triple quotes
    "secret = rb" + _S3 + "\n{v}\n" + _S3,                                   # Python bytes raw prefix
    "val apiToken = " + _Q3 + "\n    {v}\n" + _Q3 + ".trimIndent()",         # Kotlin raw string
    "String password = " + _Q3 + "\n        {v}\n        " + _Q3 + ";",      # Java text block
    "val secret = s" + _Q3 + "\n  {v}\n" + _Q3,                              # Scala interpolator
    "let token = #" + _Q3 + "\n{v}\n" + _Q3 + "#",                           # Swift extended delimiter
    "private_key: ~S" + _Q3 + "\n  {v}\n  " + _Q3,                           # Elixir sigil heredoc
    "password = <<~EOS.chomp\n  {v}\nEOS",                                   # Ruby squiggly heredoc + method
    "export API_TOKEN=$(cat <<'EOF'\n{v}\nEOF\n)",                           # Bash here-document in $(...)
    'secret = <<-EOT\n    {v}\n    EOT',                                     # HCL heredoc
    "$password = <<<'NOW'\n{v}\nNOW;",                                       # PHP nowdoc
    '$token = @"\n{v}\n"@',                                                  # PowerShell here-string
    'var secret = $' + _Q3 + "\n    {v}\n    " + _Q3 + ";",                  # C# raw string literal
    'let token = r#"\n{v}\n"#;',                                             # Rust raw string
    'auto secret = R"KEY(\n{v}\n)KEY";',                                     # C++ raw string
    "const apiKey = `\n  {v}\n`;",                                          # JS template literal / Go raw string
    "password = " + _S3 + "\n{v}\n" + _S3,                                   # TOML multi-line literal
    "local secret = [==[\n{v}\n]==]",                                        # Lua long bracket
    "password = ''\n  {v}\n'';",                                            # Nix indented string
    "api_key: |2-\n    {v}\n",                                               # YAML block scalar, indentation + chomp
    "token: >+\n  {v}\n",                                                    # YAML folded, keep
    "<password><![CDATA[{v}]]></password>",                                  # XML CDATA
])
def test_credential_slot_multiline_literals(shape):
    v = _slot_value(shape)
    r = strip_secrets(shape.format(v=v))
    assert v not in r.text


@pytest.mark.parametrize("first", list("bBrRfFuU"))
def test_a_value_starting_with_a_string_prefix_letter_is_covered_from_its_first_character(first):
    # H4 root cause (2026-10-02): the optional string prefix (b"", r"") matched without a quote and swallowed the
    # value's first character. A prefix is now taken only together with its quote.
    v = first + _slot_value(first)[1:]
    for text in (f"spring.datasource.password={v}\n", f"Password={v};Encrypt=True", f'token = {first}"{v[1:]}"'):
        r = strip_secrets(text)
        secret = v if not text.startswith("token") else v[1:]
        start = text.index(secret)
        covered = {i for f in r.findings for i in range(f.start, f.end)}
        assert all(i in covered for i in range(start, start + len(secret))), text


def test_a_chained_assignment_is_judged_on_its_last_value():
    # H4 dotenv miss (2026-10-02): `_authToken=NAME=value` made one >64-char candidate that the entropy layer skipped.
    import random
    r = random.Random(5)
    v = "".join(r.choice(string.ascii_letters + string.digits + "_-") for _ in range(48))
    text = f"//registry.npmjs.org/:_authToken=CLIENT_SECRET={v}\n"
    res = strip_secrets(text)
    assert v not in res.text and "CLIENT_SECRET=" in res.text      # the name and '=' survive; the value does not
