"""Tests for models/anthropic_messages_provider.py (D-0028, D-0021; SI-2, SI-3). The SDK client is faked and
answers with real `anthropic.types.Message` objects; one test uses the real SDK against a closed localhost port to
check that error paths never carry the key. No network, no real key."""
import types
import uuid
from decimal import Decimal

import anthropic
import httpx2 as httpx
import pytest
from anthropic.types import Message as SdkMessage

from nacre.core.model_provider import Message, ModelParams, ModelRequest, ProviderError
from nacre.models.anthropic_messages_provider import PINNED_MODELS, AnthropicMessagesProvider
from nacre.models.call_model import ModelCallRefused, call_model, load_prices
from nacre.models.set_model_policy import set_model_policy

CLAUDE = "claude-haiku-4-5-20251001"
SCHEMA = {"type": "json_schema", "name": "answer", "strict": True,
          "schema": {"type": "object", "additionalProperties": False, "required": ["a"],
                     "properties": {"a": {"type": "string"}}}}


class FakeMessages:
    def __init__(self, result=None, exc=None):
        self.result, self.exc, self.kwargs, self.calls = result, exc, None, 0

    def create(self, **kwargs):
        self.calls += 1
        self.kwargs = kwargs
        if self.exc:
            raise self.exc
        return self.result


def _client(**kw):
    messages = FakeMessages(**kw)
    return types.SimpleNamespace(messages=messages), messages


def _msg(text="ok", stop_reason="end_turn", stop_details=None, content=None, **usage):
    u = {"input_tokens": 10, "output_tokens": 3, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    u.update(usage)
    return SdkMessage.model_validate({
        "id": "msg_x", "type": "message", "role": "assistant", "model": CLAUDE, "stop_reason": stop_reason,
        "stop_sequence": None, "stop_details": stop_details, "usage": u,
        "content": content if content is not None else [{"type": "text", "text": text}]})


def _req(params=None, model=CLAUDE, provider="anthropic", system=None, content="hi"):
    return ModelRequest(provider, model, (Message("user", content),), params or ModelParams(max_tokens=50), "seat",
                        system=system)


def _caps(**caps):
    prices = load_prices()
    prices["models"][CLAUDE] = dict(prices["models"][CLAUDE], capabilities=caps)
    return prices


# ---- request mapping (D-0028 §1) ----

def test_request_and_response_mapping():
    client, m = _client(result=_msg(text="ok"))
    r = _req(ModelParams(max_tokens=50, temperature=0.0, response_format=SCHEMA), system="be brief")
    out = AnthropicMessagesProvider(client).complete(r, timeout_s=7.5)
    assert m.kwargs == {"model": CLAUDE, "max_tokens": 50, "messages": [{"role": "user", "content": "hi"}],
                        "timeout": 7.5, "system": "be brief", "extra_body": {"temperature": 0.0},
                        "output_config": {"format": {"type": "json_schema", "schema": SCHEMA["schema"]}}}
    assert (out.text, out.finish_reason, out.response_id, out.model_reported) == ("ok", "end_turn", "msg_x", CLAUDE)


def test_provider_defaults_are_not_sent_and_the_deprecated_output_format_never_is():
    client, m = _client(result=_msg())
    AnthropicMessagesProvider(client).complete(_req(), timeout_s=1)
    assert set(m.kwargs) == {"model", "max_tokens", "messages", "timeout"}
    client, m = _client(result=_msg())
    AnthropicMessagesProvider(client).complete(_req(ModelParams(max_tokens=5, response_format=SCHEMA)), timeout_s=1)
    assert "output_format" not in m.kwargs and m.kwargs["output_config"]["format"]["type"] == "json_schema"


def test_multi_turn_messages_keep_order_and_roles():
    client, m = _client(result=_msg())
    r = ModelRequest("anthropic", CLAUDE, (Message("user", "a"), Message("assistant", "b"), Message("user", "c")),
                     ModelParams(max_tokens=5), "seat")
    AnthropicMessagesProvider(client).complete(r, timeout_s=1)
    assert m.kwargs["messages"] == [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"},
                                    {"role": "user", "content": "c"}]


@pytest.mark.parametrize("params,caps", [
    (ModelParams(max_tokens=5, temperature=0.0), {"temperature": False, "json_schema": True}),
    (ModelParams(max_tokens=5, temperature=0.0), {"json_schema": True}),                 # missing = not allowed
    (ModelParams(max_tokens=5, response_format=SCHEMA), {"temperature": True, "json_schema": False}),
    (ModelParams(max_tokens=5, seed=7), {"temperature": True, "json_schema": True}),
    (ModelParams(max_tokens=5, top_p=0.5), {"temperature": True, "json_schema": True}),
    (ModelParams(max_tokens=5, response_format={"type": "json_object"}), {"temperature": True, "json_schema": True}),
    (ModelParams(max_tokens=5, response_format=dict(SCHEMA, strict=False)), {"temperature": True, "json_schema": True}),
])
def test_unsupported_parameters_are_refused_before_any_network_call(params, caps):
    client, m = _client(result=_msg())
    with pytest.raises(ProviderError) as e:
        AnthropicMessagesProvider(client, prices=_caps(**caps)).complete(_req(params), timeout_s=1)
    assert not e.value.retryable and e.value.error_class == "unsupported_param" and m.calls == 0


@pytest.mark.parametrize("request_", [
    _req(model="claude-haiku-4-5"),                       # the alias: not a pin (D-0028 option c rejected)
    _req(model="claude-sonnet-5"),                        # option (b) is closed
    _req(provider="openai"),
])
def test_only_the_pinned_claude_seat_is_called(request_):
    client, m = _client(result=_msg())
    with pytest.raises(ProviderError) as e:
        AnthropicMessagesProvider(client).complete(request_, timeout_s=1)
    assert not e.value.retryable and m.calls == 0
    assert PINNED_MODELS == {CLAUDE}


def test_a_model_without_a_capabilities_row_is_refused_fail_closed():
    prices = load_prices()
    del prices["models"][CLAUDE]["capabilities"]
    client, m = _client(result=_msg())
    with pytest.raises(ProviderError) as e:
        AnthropicMessagesProvider(client, prices=prices).complete(_req(), timeout_s=1)
    assert e.value.error_class == "unsupported_model" and m.calls == 0
    prices["models"].pop(CLAUDE)
    with pytest.raises(ProviderError):
        AnthropicMessagesProvider(client, prices=prices).complete(_req(), timeout_s=1)
    assert m.calls == 0


def test_the_price_table_entry_is_the_one_read_from_the_live_page():
    e = load_prices()["models"][CLAUDE]
    assert (e["provider"], e["input"], e["cached_input"], e["cache_write_5m"], e["cache_write_1h"], e["output"]) == \
        ("anthropic", "1", "0.10", "1.25", "2", "5")
    assert e["capabilities"] == {"temperature": True, "json_schema": True}
    assert "platform.claude.com/docs/en/about-claude/pricing" in e["source"] and "2026-10-02" in e["source"]


# ---- stop reasons and content ----

def test_end_turn_is_a_complete_response_and_text_blocks_are_joined():
    client, _ = _client(result=_msg(content=[{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]))
    out = AnthropicMessagesProvider(client).complete(_req(), timeout_s=1)
    assert (out.text, out.finish_reason) == ("ab", "end_turn")


def test_max_tokens_is_truncated_and_not_retryable():
    client, _ = _client(result=_msg(stop_reason="max_tokens"))
    with pytest.raises(ProviderError) as e:
        AnthropicMessagesProvider(client).complete(_req(), timeout_s=1)
    assert (e.value.error_class, e.value.retryable) == ("truncated", False)


@pytest.mark.parametrize("details,label", [
    ({"type": "refusal", "category": "cyber", "explanation": "x"}, "refused:cyber"),
    ({"type": "refusal", "category": None, "explanation": None}, "refused:none"),
    (None, "refused:none"),
])
def test_refusal_is_not_retryable_and_records_the_category(details, label):
    client, _ = _client(result=_msg(stop_reason="refusal", stop_details=details))
    with pytest.raises(ProviderError) as e:
        AnthropicMessagesProvider(client).complete(_req(), timeout_s=1)
    assert (e.value.error_class, e.value.retryable) == (label, False)


@pytest.mark.parametrize("reason", ["stop_sequence", "tool_use", "pause_turn", "model_context_window_exceeded", None])
def test_any_other_stop_reason_is_an_error(reason):
    client, _ = _client(result=_msg(stop_reason=reason))
    with pytest.raises(ProviderError) as e:
        AnthropicMessagesProvider(client).complete(_req(), timeout_s=1)
    assert e.value.error_class == f"unexpected_stop:{reason or 'none'}" and not e.value.retryable


def test_no_server_side_fallbacks_are_requested_or_accepted():
    client, m = _client(result=_msg())
    AnthropicMessagesProvider(client).complete(_req(ModelParams(max_tokens=5, temperature=0.0)), timeout_s=1)
    sent = repr(m.kwargs)
    assert "fallback" not in sent and "betas" not in m.kwargs and not hasattr(client, "beta")
    non_text = types.SimpleNamespace(id="msg_x", model=CLAUDE, stop_reason="end_turn", stop_details=None,
                                     usage=_msg().usage, content=[types.SimpleNamespace(type="fallback")])
    client, _ = _client(result=non_text)
    with pytest.raises(ProviderError) as e:
        AnthropicMessagesProvider(client).complete(_req(), timeout_s=1)
    assert e.value.error_class == "unexpected_content" and not e.value.retryable


# ---- usage and cost ----

def test_usage_counts_cache_reads_as_cached_input():
    client, _ = _client(result=_msg(input_tokens=1000, cache_read_input_tokens=200, output_tokens=500))
    u = AnthropicMessagesProvider(client).complete(_req(), timeout_s=1).usage
    assert (u.input_tokens, u.output_tokens, u.cached_input_tokens) == (1200, 500, 200)


def test_cache_write_tokens_cannot_be_priced_and_fail_closed():
    client, _ = _client(result=_msg(cache_creation_input_tokens=64))
    with pytest.raises(ProviderError) as e:
        AnthropicMessagesProvider(client).complete(_req(), timeout_s=1)
    assert e.value.error_class == "unpriced_usage" and not e.value.retryable


@pytest.fixture
def claude_world(world, provider):
    with world["open"](world["owner"]) as s:
        set_model_policy(s, provider, org_id=world["org"], allowed=[("anthropic", CLAUDE)],
                         idempotency_key=str(uuid.uuid4()))
    return world


def test_cost_comes_from_the_price_table_through_call_model(claude_world, provider):
    w = claude_world
    client, _ = _client(result=_msg(input_tokens=1000, cache_read_input_tokens=200, output_tokens=500))
    with w["open"](w["owner"]) as s:
        r = call_model(s, provider, AnthropicMessagesProvider(client), _req(), source_event_ids=[w["source"]()],
                       run_id=uuid.uuid4(), sleep=lambda _: None)
    assert r.cost_usd == Decimal("0.00352")      # 1000 * 1/1M + 200 * 0.10/1M + 500 * 5/1M


def test_no_price_entry_means_no_call(claude_world, provider):
    w = claude_world
    prices = load_prices()
    prices["models"].pop(CLAUDE)
    client, m = _client(result=_msg())
    with w["open"](w["owner"]) as s, pytest.raises(ModelCallRefused, match="no price entry"):
        call_model(s, provider, AnthropicMessagesProvider(client, prices=prices), _req(),
                   source_event_ids=[w["source"]()], run_id=uuid.uuid4(), prices=prices)
    assert m.calls == 0


def test_si3_anthropic_is_default_deny_per_org(world, provider):
    client, m = _client(result=_msg())
    world["allow"]("gpt-4o-mini-2024-07-18")                   # an openai allow-list says nothing about anthropic
    with world["open"](world["owner"]) as s, pytest.raises(ModelCallRefused, match="default deny"):
        call_model(s, provider, AnthropicMessagesProvider(client), _req(), source_event_ids=[world["source"]()],
                   run_id=uuid.uuid4())
    assert m.calls == 0


def test_a_refusal_is_recorded_once_with_its_category_and_not_retried(claude_world, provider):
    w = claude_world
    client, m = _client(result=_msg(stop_reason="refusal", stop_details={"type": "refusal", "category": "bio",
                                                                          "explanation": None}))
    with w["open"](w["owner"]) as s:
        r = call_model(s, provider, AnthropicMessagesProvider(client), _req(), source_event_ids=[w["source"]()],
                       run_id=uuid.uuid4(), sleep=lambda _: None)
    assert r.attempts == 1 and m.calls == 1 and r.error.error_class == "refused:bio"
    assert r.cost_usd == Decimal("0.000025") and r.cost_basis == "usage"     # billed (D-0021 am. 2): 10 * 1 + 3 * 5


# ---- errors, retries, keys (SI-2) ----

def _status_error(cls, code):
    resp = httpx.Response(code, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
    return cls("boom", response=resp, body=None)


@pytest.mark.parametrize("exc,retryable", [
    (lambda: _status_error(anthropic.RateLimitError, 429), True),
    (lambda: _status_error(anthropic.InternalServerError, 500), True),
    (lambda: _status_error(anthropic.OverloadedError, 529), True),
    (lambda: _status_error(anthropic.BadRequestError, 400), False),
    (lambda: _status_error(anthropic.AuthenticationError, 401), False),
    (lambda: _status_error(anthropic.NotFoundError, 404), False),
    (lambda: anthropic.APITimeoutError(request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")), True),
])
def test_errors_are_classified(exc, retryable):
    client, _ = _client(exc=exc())
    with pytest.raises(ProviderError) as e:
        AnthropicMessagesProvider(client).complete(_req(), timeout_s=1)
    assert e.value.retryable is retryable and "boom" not in str(e.value)        # SDK message text never carried
    assert e.value.__cause__ is None and e.value.__suppress_context__


def test_the_default_client_has_sdk_retries_off(monkeypatch):
    made = []

    class Recorder:
        def __init__(self, **kw):
            made.append(kw)
            self.messages = FakeMessages(result=_msg())
    monkeypatch.setattr(anthropic, "Anthropic", Recorder)
    AnthropicMessagesProvider().complete(_req(), timeout_s=1)
    assert made == [{"max_retries": 0}]                         # no key passed: the SDK reads ANTHROPIC_API_KEY


def test_si2_the_key_never_appears_in_errors_from_the_real_sdk():
    canary = "sk-ant-" + "canary" + "0123456789abcdef" * 3                 # built at runtime
    client = anthropic.Anthropic(api_key=canary, base_url="http://127.0.0.1:9", max_retries=0)
    with pytest.raises(ProviderError) as e:
        AnthropicMessagesProvider(client).complete(_req(), timeout_s=2)
    assert e.value.retryable and canary not in repr(e.value) and canary not in str(e.value)
    assert canary not in str(e.value.__context__ or "") and e.value.__cause__ is None
