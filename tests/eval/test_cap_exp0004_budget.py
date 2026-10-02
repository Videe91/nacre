"""Tests for eval/cap_exp0004_budget.py: the $15 hard cap fails closed BEFORE a call that could exceed it, actual
costs accumulate, billed failures are charged what call_model records (usage, else the worst case; unbilled ones 0,
D-0021 amendment 2), the cap holds on the recorded costs, and replay providers are never capped."""
import json
import types
import uuid
from decimal import Decimal

import httpx2 as httpx
import openai
import pytest

from exp0004_kit import EchoFake
from phase2_kit import world
from nacre.capture.record_decision import record_decision
from nacre.core.event import ActorKind, Source
from nacre.core.model_provider import CallPolicy, Message, ModelParams, ModelRequest, ProviderError, Usage
from nacre.eval import cap_exp0004_budget as B
from nacre.ledger.append_event import Authorship
from nacre.ledger.read_stream import read_stream
from nacre.models.call_model import call_model, load_prices
from nacre.models.openai_responses_provider import OpenAIResponsesProvider

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


class _Fails:
    name, replay = "openai", False

    def __init__(self, exc):
        self.exc = exc

    def complete(self, request, *, timeout_s):
        raise self.exc


def test_a_billed_failure_without_usage_is_charged_its_worst_case():
    c = B.CappedProvider(_Fails(ProviderError("timeout", retryable=True, error_class="APITimeoutError", billed=True)),
                         Decimal("1"), PRICES)
    with pytest.raises(ProviderError):
        c.complete(_req(), timeout_s=1)
    assert c.spent == B.worst_case_cost(_req(), PRICES)


def test_a_billed_failure_with_usage_is_charged_that_usage_and_an_unbilled_one_nothing():
    refused = ProviderError("refused", retryable=False, error_class="refused:none", usage=Usage(100, 20, None))
    c = B.CappedProvider(_Fails(refused), Decimal("1"), PRICES)
    with pytest.raises(ProviderError):
        c.complete(_req(), timeout_s=1)
    assert c.spent == (Decimal(100) * Decimal("0.15") + Decimal(20) * Decimal("0.60")) / Decimal(1_000_000)
    c = B.CappedProvider(_Fails(ProviderError("refused", retryable=True, error_class="APIConnectionError")),
                         Decimal("1"), PRICES)
    with pytest.raises(ProviderError):
        c.complete(_req(), timeout_s=1)
    assert c.spent == 0 and c.calls == 1


def test_any_other_exception_is_charged_the_worst_case():
    c = B.CappedProvider(_Fails(KeyError("bug")), Decimal("1"), PRICES)
    with pytest.raises(KeyError):
        c.complete(_req(), timeout_s=1)
    assert c.spent == B.worst_case_cost(_req(), PRICES)


def test_the_worst_case_counts_the_json_schema_bytes():
    schema = {"type": "json_schema", "name": "r", "strict": True, "schema": {"type": "object"}}
    plain, with_schema = _req(), ModelRequest("openai", "gpt-4o-mini-2024-07-18", (Message("user", "x" * 1000),),
                                              ModelParams(400, response_format=schema), "eval.exp0004.transfer.N")
    extra = len(json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()) + 16
    p = PRICES["models"]["gpt-4o-mini-2024-07-18"]
    assert B.worst_case_cost(with_schema, PRICES) - B.worst_case_cost(plain, PRICES) == \
        Decimal(extra) * Decimal(p["input"]) / Decimal(1_000_000)


def test_a_replay_provider_is_not_capped():
    class Replay:
        name, replay = "recorded", True
    with pytest.raises(ValueError):
        B.CappedProvider(Replay())


# ---- D-0021 amendment 2: the cap is enforced on the costs call_model RECORDS (real adapter, fake SDK, no network) ----

def _openai_script(*items):
    class Responses:
        def __init__(self):
            self.items, self.calls = list(items), 0

        def create(self, **kwargs):
            self.calls += 1
            item = self.items.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
    responses = Responses()
    return types.SimpleNamespace(responses=responses), responses


def _resp(usage=True, status="completed", reason=None):
    u = types.SimpleNamespace(input_tokens=300, output_tokens=400, input_tokens_details=None) if usage else None
    return types.SimpleNamespace(output_text='{"answer": null, "ask": true}', status=status, id="r", model=MODEL,
                                 incomplete_details=types.SimpleNamespace(reason=reason) if reason else None, usage=u)


MODEL = "gpt-4o-mini-2024-07-18"


def test_the_cap_is_enforced_on_recorded_costs_including_billed_failures(org, provider):
    w = world(org, provider)
    stream = w["new_scope"]()
    with w["session"]() as s:
        anchor = record_decision(s, provider, stream_id=stream, actor_kind=ActorKind.AGENT, actor_id=uuid.uuid4(),
                                 source=Source.CHAT, authorship=Authorship.SCOPE_PRINCIPAL,
                                 idempotency_key=str(uuid.uuid4()), decision_text="anchor").envelope.event_id
    http_req = httpx.Request("POST", "https://api.openai.com/v1/responses")
    client, sdk = _openai_script(openai.APITimeoutError(request=http_req),               # billed, no usage: worst
                                 _resp(status="incomplete", reason="max_output_tokens"),  # truncated: its usage
                                 openai.APIConnectionError(request=http_req),             # never reached: 0
                                 _resp(usage=False),                                      # no usage: worst
                                 _resp())
    req = _req("x" * 1000)
    worst = B.worst_case_cost(req, PRICES)
    usage_cost = (Decimal(300) * Decimal("0.15") + Decimal(400) * Decimal("0.60")) / Decimal(1_000_000)
    cap = 3 * worst + usage_cost - Decimal("0.0000000001")     # the third call's reservation does not fit
    capped = B.CappedProvider(OpenAIResponsesProvider(client), cap, PRICES)
    totals = []
    for _ in range(2):                         # call 1: timeout, then truncation; call 2: connection, then no usage
        with w["session"]() as s:
            call = call_model(s, provider, capped, req, source_event_ids=[anchor], run_id=uuid.uuid4(),
                              policy=CallPolicy(max_attempts=2, backoff_s=0), sleep=lambda _: None)
        totals.append((call.cost_usd, call.cost_basis))
    with w["session"]() as s:
        bodies = [e.body["content"] for e in read_stream(s, provider, stream)
                  if isinstance(e.body["content"], dict) and e.body["content"].get("kind") == "model_call"]
    assert [(b["status"], b["cost_basis"], b.get("billed")) for b in bodies] == [
        ("error", "worst_case", True), ("ok", "usage", None), ("error", "none", False), ("error", "worst_case", True)]
    assert [Decimal(b["cost_usd"]) for b in bodies] == [worst, usage_cost, 0, worst]
    assert sum(Decimal(b["cost_usd"]) for b in bodies) == capped.spent == sum(c for c, _ in totals)
    assert totals == [(worst + usage_cost, "worst_case"), (worst, "worst_case")] and sdk.calls == 4
    with w["session"]() as s, pytest.raises(B.BudgetExceeded):     # spent + worst > cap: never sent
        call_model(s, provider, capped, req, source_event_ids=[anchor], run_id=uuid.uuid4())
    assert capped.tripped and sdk.calls == 4
