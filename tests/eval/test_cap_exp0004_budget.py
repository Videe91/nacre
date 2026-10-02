"""Tests for eval/cap_exp0004_budget.py: the $15 hard cap fails closed BEFORE a call that could exceed it, actual
costs accumulate, failed calls are charged their worst case, and replay providers are never capped."""
from decimal import Decimal

import pytest

from exp0004_kit import EchoFake
from nacre.core.model_provider import Message, ModelParams, ModelRequest, ProviderError
from nacre.eval import cap_exp0004_budget as B
from nacre.models.call_model import load_prices

PRICES = load_prices()


def _req(text="x" * 1000, max_tokens=400):
    return ModelRequest("openai", "gpt-4o-mini-2024-07-18", (Message("user", text),), ModelParams(max_tokens),
                        "eval.exp0004.transfer.N")


def test_the_cap_is_fifteen_dollars():
    assert B.HARD_CAP_USD == Decimal("15")


def test_worst_case_is_bytes_plus_overhead_at_input_price_plus_max_tokens_at_output_price():
    p = PRICES["models"]["gpt-4o-mini-2024-07-18"]
    expected = (Decimal(1000 + 32) * Decimal(p["input"]) + Decimal(400) * Decimal(p["output"])) / Decimal(1_000_000)
    assert B.worst_case_cost(_req(), PRICES) == expected
    assert B.worst_case_cost(_req("é" * 10), PRICES) > B.worst_case_cost(_req("e" * 10), PRICES)   # bytes, not chars


def test_actual_costs_accumulate_from_reported_usage():
    c = B.CappedProvider(EchoFake(), Decimal("1"), PRICES)
    c.complete(_req("Memory:\n(none)\n\nTask:\nq"), timeout_s=1)
    assert c.spent == (Decimal(100) * Decimal("0.15") + Decimal(20) * Decimal("0.60")) / Decimal(1_000_000)
    assert c.calls == 1 and not c.tripped


def test_a_call_that_could_exceed_the_cap_is_never_sent():
    inner = EchoFake()
    c = B.CappedProvider(inner, B.worst_case_cost(_req(), PRICES), PRICES)
    c.complete(_req("Memory:\n(none)\n\nTask:\nq"), timeout_s=1)
    with pytest.raises(B.BudgetExceeded):
        c.complete(_req(), timeout_s=1)
    assert c.tripped and len(inner.calls) == 1


def test_a_failed_call_is_charged_its_worst_case():
    class Fails:
        name, replay = "openai", False

        def complete(self, request, *, timeout_s):
            raise ProviderError("timeout", retryable=True, error_class="APITimeoutError")
    c = B.CappedProvider(Fails(), Decimal("1"), PRICES)
    with pytest.raises(ProviderError):
        c.complete(_req(), timeout_s=1)
    assert c.spent == B.worst_case_cost(_req(), PRICES)


def test_a_replay_provider_is_not_capped():
    class Replay:
        name, replay = "recorded", True
    with pytest.raises(ValueError):
        B.CappedProvider(Replay())
