"""Tests for models/openai_responses_provider.py (D-0021; SI-2). The SDK client is faked; one test uses the real SDK
against a closed localhost port to check that error paths never carry the key."""
import types

import openai
import pytest

from model_fakes import MODEL, req
from nacre.core.model_provider import Message, ModelParams, ModelRequest, ProviderError
from nacre.models.openai_responses_provider import OpenAIResponsesProvider


class FakeResponses:
    def __init__(self, result=None, exc=None):
        self.result, self.exc, self.kwargs = result, exc, None

    def create(self, **kwargs):
        self.kwargs = kwargs
        if self.exc:
            raise self.exc
        return self.result


def _client(**kw):
    responses = FakeResponses(**kw)
    return types.SimpleNamespace(responses=responses), responses


def _result(**over):
    base = dict(output_text="ok", status="completed", incomplete_details=None, id="resp_x", model=MODEL,
                usage=types.SimpleNamespace(input_tokens=10, output_tokens=3,
                                            input_tokens_details=types.SimpleNamespace(cached_tokens=4)))
    base.update(over)
    return types.SimpleNamespace(**base)


def test_request_and_response_mapping():
    client, responses = _client(result=_result())
    r = ModelRequest("openai", MODEL, (Message("user", "hi"),), ModelParams(max_tokens=50, temperature=0.0),
                     "seat", system="be brief")
    out = OpenAIResponsesProvider(client).complete(r, timeout_s=7.5)
    assert responses.kwargs == {"model": MODEL, "max_output_tokens": 50, "input": [{"role": "user", "content": "hi"}],
                                "timeout": 7.5, "instructions": "be brief", "temperature": 0.0}
    assert (out.text, out.finish_reason, out.response_id, out.model_reported) == ("ok", "completed", "resp_x", MODEL)
    assert (out.usage.input_tokens, out.usage.output_tokens, out.usage.cached_input_tokens) == (10, 3, 4)


def test_provider_defaults_are_not_sent():
    client, responses = _client(result=_result())
    OpenAIResponsesProvider(client).complete(req(), timeout_s=1)
    assert "temperature" not in responses.kwargs and "top_p" not in responses.kwargs


def test_incomplete_responses_say_why():
    client, _ = _client(result=_result(status="incomplete", incomplete_details=types.SimpleNamespace(reason="max_output_tokens")))
    assert OpenAIResponsesProvider(client).complete(req(), timeout_s=1).finish_reason == "incomplete:max_output_tokens"


def test_an_unsupported_parameter_is_refused_not_dropped():
    client, responses = _client(result=_result())
    r = ModelRequest("openai", MODEL, (Message("user", "x"),), ModelParams(max_tokens=5, seed=7), "seat")
    with pytest.raises(ProviderError) as e:
        OpenAIResponsesProvider(client).complete(r, timeout_s=1)
    assert not e.value.retryable and responses.kwargs is None


def _status_error(cls, code):
    import httpx2 as httpx
    resp = httpx.Response(code, request=httpx.Request("POST", "https://api.openai.com/v1/responses"))
    return cls("boom", response=resp, body=None)


@pytest.mark.parametrize("exc,retryable", [
    (lambda: _status_error(openai.RateLimitError, 429), True),
    (lambda: _status_error(openai.InternalServerError, 500), True),
    (lambda: _status_error(openai.BadRequestError, 400), False),
    (lambda: _status_error(openai.AuthenticationError, 401), False),
])
def test_errors_are_classified(exc, retryable):
    client, _ = _client(exc=exc())
    with pytest.raises(ProviderError) as e:
        OpenAIResponsesProvider(client).complete(req(), timeout_s=1)
    assert e.value.retryable is retryable and "boom" not in str(e.value)       # SDK message text never carried


def test_si2_the_key_never_appears_in_errors_from_the_real_sdk():
    canary = "sk-" + "canary" + "0123456789abcdef" * 3                       # built at runtime
    client = openai.OpenAI(api_key=canary, base_url="http://127.0.0.1:9/v1", max_retries=0)
    with pytest.raises(ProviderError) as e:
        OpenAIResponsesProvider(client).complete(req(), timeout_s=2)
    assert e.value.retryable and canary not in repr(e.value) and canary not in str(e.value.__context__ or "")
