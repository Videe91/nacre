"""Tests for scripts/measure_token_format.py: its output must never contain token text."""
import importlib.util
import random
import string
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("measure", ROOT / "scripts/measure_token_format.py")
measure = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(measure)
rng = random.Random(7)


def _token(prefix, n, alphabet=string.ascii_letters + string.digits):
    return prefix + "".join(rng.choice(alphabet) for _ in range(n))


def _run(inputs, secrets):
    lines, out = iter(inputs), []
    code = measure.main(read_secret=lambda _: next(secrets, ""), read_line=lambda _: next(lines),
                        out=out.append, today=date(2026, 9, 30))
    return code, "\n".join(out)


def _assert_no_token_text(output, token, typed_prefix):
    secret_part = token[len(typed_prefix):] if token.startswith(typed_prefix) else token
    for i in range(len(secret_part) - 3):
        assert secret_part[i:i + 4] not in output, "output leaked token text"


@pytest.mark.parametrize("prefix,length,alphabet", [
    ("sk_live_", 99, string.ascii_letters + string.digits),
    ("xoxb-", 50, string.digits + "-" + string.ascii_letters),
    ("", 32, "0123456789abcdef"),                      # no prefix at all (e.g. a plain hex key)
    ("", 60, string.ascii_letters + string.digits + "."),
])
def test_output_never_contains_token_text(prefix, length, alphabet):
    tokens = [_token(prefix, length, alphabet) for _ in range(3)]
    code, output = _run(["Prov", "type", prefix], iter(tokens))
    assert code == 0
    for t in tokens:
        _assert_no_token_text(output, t, prefix)


def test_a_wrong_typed_prefix_does_not_leak_the_real_one():
    real = _token("abcd_", 30)
    code, output = _run(["Prov", "type", "zzzz_"], iter([real]))
    assert "matched 0/1" in output
    _assert_no_token_text(output, real, "")


def test_facts():
    tokens = ["sk_test_" + "a1B2" * 5, "sk_test_" + "c3D4_e" * 4]
    f = measure.describe(tokens, "sk_test_")
    assert (f["prefix_matches"], f["length_min"], f["length_max"]) == (2, 28, 32)
    assert f["classes"] == ["a-z", "A-Z", "0-9"] and f["punctuation"] == "_"


def test_dot_segments_are_lengths_only():
    f = measure.describe(["aaaa.bbbbbb.cc", "dddd.eeeeee.ff"], "")
    assert f["dot_segments"] == [[4, 6, 2]]


def test_one_sample_is_provisional():
    _, output = _run(["Prov", "type", "p_"], iter(["p_" + "x9" * 10]))
    assert "provisional: 1 sample" in output and "2026-09-30" in output


def test_whitespace_is_refused():
    code, output = _run(["Prov", "type", ""], iter(["abc def"]))
    assert code == 1 and "Refused" in output and "abc" not in output


def test_no_tokens():
    code, output = _run(["Prov", "type", ""], iter([]))
    assert code == 1
