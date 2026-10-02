"""Tests for ledger/find_credential_proximity.py (owner, gate item 12 re-plan, 2026-10-02): a credential word, then a
qualifying token in the same statement or on the first non-empty line after an opener, whatever the syntax between.
Secret-shaped values are built at runtime."""
import random
import string

import pytest

from nacre.ledger.find_credential_proximity import RULE_ID, proximity_findings
from nacre.ledger.strip_secrets import strip_secrets


def _v(seed, alphabet=string.ascii_letters + string.digits, n=32):
    r = random.Random(seed)
    return "7" + "".join(r.choice(alphabet) for _ in range(n - 1))      # always holds a digit


@pytest.mark.parametrize("shape", [
    "cache.internal.test:6379> AUTH default {v}\nOK\n",                                  # command, user, password
    "read -r -d '' DEPLOY_TOKEN <<'EOF' || true\n{v}\nEOF\n",                           # heredoc after a space
    'my $db_password = <<~"END";\n    {v}\n    END\n',                                   # opener line ends in ;
    '<add key="Webhook:SigningKey"\n     value="\n       {v}\n     " />',                # attribute pair, multi-line
    '{{"name": "API_TOKEN", "value": "{v}"}}',                                           # JSON name/value pair
    "curl --us" + "er ci:{v} https://x.test --auth-type basic\n",                    # split: the hook reads the template
    "psql 'host=db.test password={v} sslmode=require'",
    "export TOKEN; TOKEN={v}",                                                           # word then ';' then value
])
def test_values_near_a_credential_word_are_found(shape):
    v = _v(shape)
    text = shape.format(v=v)
    found = strip_secrets(text)
    assert v not in found.text, text


@pytest.mark.parametrize("text", [
    "private_key: x25519.X25519PrivateKey\n",                       # identifier holding a credential word
    "MLKEM768PrivateKey.register(rust_openssl.mlkem.MLKEM768PrivateKey)\n",
    'return len(value) == _TOKEN_HEX_LENGTH and all(c in "0123456789abcdef" for c in value)\n',   # alphabet constant
    "token_count = 1234; commit = 3f2a9c1b7d4e8f60a1b2c3d4e5f60718\n",   # value after the statement ends (;)
    "password:\n    hunter\n",                                       # too short / no digit
])
def test_non_values_are_left_alone(text):
    assert not proximity_findings(text)


def test_statement_reach_is_bounded():
    v = _v("far")
    assert proximity_findings("password " + "x " * 60 + v) == []      # starts > 100 chars after the word
    assert proximity_findings("password " + "x " * 10 + v)[0][0] == RULE_ID


def test_only_the_value_is_redacted_and_the_context_survives():
    v = _v("ctx")
    r = strip_secrets(f"AUTH default {v}\n")
    assert r.text == "AUTH default [REDACTED:credential-proximity-value]\n"


def test_a_value_with_no_credential_word_before_it_is_out_of_reach_by_design():
    v = _v("mysql")
    assert proximity_findings(f"mysql -u admin -p{v} -h db.test\n") == []   # '-p' is not a credential word



@pytest.mark.parametrize("text", ["token " * 200_000, "token = value_here; password: x\n" * 40_000,
                                  "token " + "a1" * 600_000])
def test_time_stays_linear_on_dense_megabyte_inputs(text):
    # 2026-10-02: per-word scans of the whole text made 320 kB take 18.8 s and hung the full suite; now every step is
    # bounded. Generous ceiling, still far below the quadratic behaviour (> 20 s here).
    import time
    t = time.perf_counter()
    proximity_findings(text)
    assert time.perf_counter() - t < 8.0


@pytest.mark.parametrize("text", [
    "authoritative_correction source_span=218:314 span_sha256=" + "ab12" * 16,      # 'auth' inside another word
    '{"output_tokens": 16, "response_id": "resp_' + "9f" * 20 + '"}',               # 'tokens' count; other field
    '{"kind": "token", "model": "gpt-4o-mini-2024-07-18x1"}',                         # value in another JSON field
    "password_policy: strict, digest_sha256=" + "c3" * 32,                           # nearest label is not a credential
    '"kinds": ["credential-slot/random/alnum-24"]',                                  # lowercase path is a name
    "leaked credential.\n[interpretive:i_" + "a5" * 16 + "]",                        # tail of an identifier
    "see docs.openssl.org/master/man3/PEM_read_bio_PrivateKey for the password format",
])
def test_repository_style_false_positives_are_left_alone(text):
    # 2026-10-02: the first proximity version flagged 208 of the repository's own 2,106 files (results JSON, docs).
    assert not proximity_findings(text), text
