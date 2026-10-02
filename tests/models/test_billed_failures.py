"""D-0021 amendment 2 (owner, 2026-10-02): a call the provider bills is costed even when the adapter raises for it.
Both adapters run over fake SDK clients (no network, no key): a refused, truncated or otherwise billed non-success
carries the provider's usage on its ProviderError and call_model records that cost (`cost_basis: usage`); a billed
failure without usage (missing usage, a timeout after the request was sent) records the worst case (`worst_case`); an
error that never reached the provider records 0 (`none`)."""
import types
import uuid
from decimal import Decimal

import anthropic
import httpx2 as httpx
import openai
import pytest
from anthropic.types import Message as SdkMessage

from model_fakes import MODEL, FakeProvider, ok, req
from nacre.core.event import ActorKind, EventType
from nacre.core.model_provider import CallPolicy, Message, ModelParams, ModelRequest, ProviderError, Usage
from nacre.ledger.read_stream import read_stream
from nacre.models.anthropic_messages_provider import AnthropicMessagesProvider
from nacre.models.call_model import attempt_cost, call_model, load_prices, worst_case_cost
from nacre.models.openai_responses_provider import OpenAIResponsesProvider
from nacre.models.set_model_policy import set_model_policy

CLAUDE = "claude-haiku-4-5-20251001"
PRICES = load_prices()
M = Decimal(1_000_000)


class Script:
    def __init__(self, *items):
        self.items, self.calls = list(items), 0

    def create(self, **kwargs):
        self.calls += 1
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _claude(stop_reason="end_turn", usage=None, details=None):
    u = {"input_tokens": 1000, "output_tokens": 50, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    u.update(usage or {})
    return SdkMessage.model_validate({
        "id": "msg_x", "type": "message", "role": "assistant", "model": CLAUDE, "stop_reason": stop_reason,
        "stop_sequence": None, "stop_details": details, "usage": u, "content": [{"type": "text", "text": "x"}]})


def _gpt(usage=True, status="completed", reason=None, **over):
    u = types.SimpleNamespace(input_tokens=1000, output_tokens=500, input_tokens_details=None) if usage else None
    base = dict(output_text="x", status=status, id="r", model=MODEL, usage=u,
                incomplete_details=types.SimpleNamespace(reason=reason) if reason else None)
    return types.SimpleNamespace(**(base | over))


def _a_req(max_tokens=200):
    return ModelRequest("anthropic", CLAUDE, (Message("user", "hi"),), ModelParams(max_tokens=max_tokens), "seat")


def _http(url):
    return httpx.Request("POST", url)


def _claude_cost(inp=1000, out=50):
    return (Decimal(inp) * 1 + Decimal(out) * 5) / M          # input $1/M, output $5/M


def _gpt_cost(inp=1000, out=500):
    return (Decimal(inp) * Decimal("0.15") + Decimal(out) * Decimal("0.60")) / M


@pytest.fixture
def both(world, provider):
    with world["open"](world["owner"]) as s:
        set_model_policy(s, provider, org_id=world["org"], allowed=[("openai", MODEL), ("anthropic", CLAUDE)],
                         idempotency_key=str(uuid.uuid4()))
    return world


def _run(w, provider, adapter, request, attempts=1):
    with w["open"](w["owner"]) as s:
        call = call_model(s, provider, adapter, request, source_event_ids=[w["source"]()], run_id=uuid.uuid4(),
                          policy=CallPolicy(max_attempts=attempts, backoff_s=0), sleep=lambda _: None)
    with w["open"](w["owner"]) as s:
        bodies = [e.body["content"] for e in read_stream(s, provider, w["proj"])
                  if e.envelope.event_type == EventType.RESULT and e.envelope.actor_kind == ActorKind.MODEL]
    return call, bodies


# ---- the carrier: ProviderError(billed, usage), backwards compatible ----

def test_provider_error_defaults_to_unbilled_and_usage_implies_billed():
    e = ProviderError("x", retryable=True, error_class="rate_limit")
    assert (e.billed, e.usage) == (False, None)
    e = ProviderError("x", retryable=False, error_class="truncated", usage=Usage(1, 2, None))
    assert e.billed is True and e.usage == Usage(1, 2, None)


def test_attempt_cost_bases():
    r = req()
    assert attempt_cost(PRICES, r, response=ok(inp=1000, out=500)) == (_gpt_cost(), "usage")
    assert attempt_cost(PRICES, r, error=ProviderError("x", retryable=False, error_class="c")) == (0, "none")
    billed = ProviderError("x", retryable=False, error_class="c", usage=Usage(1000, 500, None))
    assert attempt_cost(PRICES, r, error=billed) == (_gpt_cost(), "usage")
    worst = ProviderError("x", retryable=True, error_class="APITimeoutError", billed=True)
    assert attempt_cost(PRICES, r, error=worst) == (worst_case_cost(PRICES, r), "worst_case")


def test_the_worst_case_is_max_tokens_at_output_plus_an_input_upper_bound():
    r = ModelRequest("openai", MODEL, (Message("user", "é" * 10),), ModelParams(max_tokens=256), "seat", system="ab")
    tokens_in = 20 + 2 + 16 * 3                                    # UTF-8 bytes + 16 per part and per request
    assert worst_case_cost(PRICES, r) == (Decimal(tokens_in) * Decimal("0.15") + 256 * Decimal("0.60")) / M


# ---- Anthropic: refused / truncated / other billed stops carry usage ----

@pytest.mark.parametrize("stop,details,error_class", [
    ("max_tokens", None, "truncated"),
    ("refusal", {"type": "refusal", "category": "cyber", "explanation": None}, "refused:cyber"),
    ("pause_turn", None, "unexpected_stop:pause_turn"),
])
def test_anthropic_billed_stops_record_their_reported_usage(both, provider, stop, details, error_class):
    sdk = Script(_claude(stop, usage={"cache_read_input_tokens": 200}, details=details))
    call, (b,) = _run(both, provider, AnthropicMessagesProvider(types.SimpleNamespace(messages=sdk)), _a_req())
    expected = (Decimal(1000) * 1 + Decimal(200) * Decimal("0.10") + Decimal(50) * 5) / M
    assert call.error.error_class == error_class and not call.error.retryable and call.error.billed
    assert (b["status"], b["cost_basis"], b["billed"], Decimal(b["cost_usd"])) == ("error", "usage", True, expected)
    assert b["usage"] == {"input_tokens": 1200, "output_tokens": 50, "cached_input_tokens": 200}
    assert (call.cost_usd, call.cost_basis, sdk.calls) == (expected, "usage", 1)


def test_anthropic_missing_usage_records_the_worst_case(both, provider):
    msg = _claude("max_tokens")
    no_usage = types.SimpleNamespace(**{k: getattr(msg, k) for k in ("id", "model", "stop_reason", "stop_details",
                                                                     "content")}, usage=None)
    call, (b,) = _run(both, provider, AnthropicMessagesProvider(types.SimpleNamespace(messages=Script(no_usage))),
                      _a_req())
    worst = worst_case_cost(PRICES, _a_req())
    assert (call.error.error_class, b["cost_basis"], Decimal(b["cost_usd"]), "usage" in b) == \
        ("truncated", "worst_case", worst, False)
    assert worst == (Decimal(2 + 32) * 1 + 200 * 5) / M


def test_anthropic_cache_write_tokens_record_the_worst_case(both, provider):
    sdk = Script(_claude(usage={"cache_creation_input_tokens": 64}))
    call, (b,) = _run(both, provider, AnthropicMessagesProvider(types.SimpleNamespace(messages=sdk)), _a_req())
    assert (call.error.error_class, b["cost_basis"]) == ("unpriced_usage", "worst_case")


def test_anthropic_timeout_is_worst_case_and_retried_and_every_attempt_sums(both, provider):
    sdk = Script(anthropic.APITimeoutError(request=_http("https://api.anthropic.com/v1/messages")), _claude())
    call, bodies = _run(both, provider, AnthropicMessagesProvider(types.SimpleNamespace(messages=sdk)), _a_req(),
                        attempts=2)
    worst = worst_case_cost(PRICES, _a_req())
    assert [(b["status"], b["cost_basis"]) for b in bodies] == [("error", "worst_case"), ("ok", "usage")]
    assert call.response.text == "x" and call.cost_usd == worst + _claude_cost() and call.cost_basis == "worst_case"


def test_anthropic_connection_and_status_errors_are_not_billed(both, provider):
    url = "https://api.anthropic.com/v1/messages"
    status = anthropic.BadRequestError("boom", response=httpx.Response(400, request=_http(url)), body=None)
    sdk = Script(anthropic.APIConnectionError(request=_http(url)), status)
    call, bodies = _run(both, provider, AnthropicMessagesProvider(types.SimpleNamespace(messages=sdk)), _a_req(),
                        attempts=2)
    assert [(b["cost_basis"], b["cost_usd"], b["billed"]) for b in bodies] == [("none", "0", False)] * 2
    assert (call.cost_usd, call.cost_basis) == (0, "none")


def test_a_pre_network_refusal_is_not_billed_and_nothing_is_sent(both, provider):
    sdk = Script(_claude())
    r = ModelRequest("anthropic", CLAUDE, (Message("user", "hi"),), ModelParams(max_tokens=5, seed=1), "seat")
    call, (b,) = _run(both, provider, AnthropicMessagesProvider(types.SimpleNamespace(messages=sdk)), r)
    assert (b["cost_basis"], b["cost_usd"], sdk.calls, call.error.error_class) == ("none", "0", 0, "unsupported_param")


# ---- OpenAI: truncated responses keep their usage; billed failures without usage are worst case ----

def test_openai_truncated_response_is_costed_from_its_usage(both, provider):
    sdk = Script(_gpt(status="incomplete", reason="max_output_tokens"))
    call, (b,) = _run(both, provider, OpenAIResponsesProvider(types.SimpleNamespace(responses=sdk)), req())
    assert call.response.finish_reason == "incomplete:max_output_tokens"
    assert (b["cost_basis"], Decimal(b["cost_usd"]), call.cost_usd) == ("usage", _gpt_cost(), _gpt_cost())


@pytest.mark.parametrize("bad", [None, types.SimpleNamespace(input_tokens=None, output_tokens=5),
                                 types.SimpleNamespace(input_tokens=10, output_tokens=5,
                                                       input_tokens_details=types.SimpleNamespace(cached_tokens=11))])
def test_openai_missing_or_malformed_usage_is_a_billed_worst_case(both, provider, bad):
    sdk = Script(_gpt(usage=False) if bad is None else types.SimpleNamespace(**(vars(_gpt()) | {"usage": bad})))
    call, (b,) = _run(both, provider, OpenAIResponsesProvider(types.SimpleNamespace(responses=sdk)), req())
    assert (call.error.error_class, call.error.retryable) == ("missing_usage", False)
    assert (b["cost_basis"], Decimal(b["cost_usd"])) == ("worst_case", worst_case_cost(PRICES, req()))


def test_openai_unmappable_response_is_billed_from_its_usage(both, provider):
    class Broken:
        status, id, model, incomplete_details = "completed", "r", MODEL, None
        usage = types.SimpleNamespace(input_tokens=1000, output_tokens=500, input_tokens_details=None)

        @property
        def output_text(self):
            raise TypeError("unexpected output item")
    call, (b,) = _run(both, provider, OpenAIResponsesProvider(types.SimpleNamespace(responses=Script(Broken()))), req())
    assert (call.error.error_class, b["cost_basis"], Decimal(b["cost_usd"])) == \
        ("unexpected_response", "usage", _gpt_cost())


def test_openai_timeout_is_worst_case_and_connection_error_is_free(both, provider):
    url = "https://api.openai.com/v1/responses"
    sdk = Script(openai.APITimeoutError(request=_http(url)), openai.APIConnectionError(request=_http(url)))
    call, bodies = _run(both, provider, OpenAIResponsesProvider(types.SimpleNamespace(responses=sdk)), req(),
                        attempts=2)
    worst = worst_case_cost(PRICES, req())
    assert [(b["error_class"], b["cost_basis"], Decimal(b["cost_usd"])) for b in bodies] == [
        ("APITimeoutError", "worst_case", worst), ("APIConnectionError", "none", 0)]
    assert (call.cost_usd, call.cost_basis) == (worst, "worst_case")


def test_a_real_sdk_connection_refused_is_not_billed():
    canary = "sk-" + "canary" + "0123456789abcdef" * 3                       # built at runtime
    client = openai.OpenAI(api_key=canary, base_url="http://127.0.0.1:9/v1", max_retries=0)
    with pytest.raises(ProviderError) as e:
        OpenAIResponsesProvider(client).complete(req(), timeout_s=2)
    assert e.value.error_class == "APIConnectionError" and not e.value.billed and e.value.usage is None


def test_replays_and_successes_keep_their_basis(both, provider):
    call, (b,) = _run(both, provider, FakeProvider([ok()]), req())
    assert (call.cost_basis, b["cost_basis"], "billed" in b) == ("usage", "usage", False)
