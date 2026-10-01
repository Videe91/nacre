"""Model-layer test helpers (a uniquely named module, never `conftest`, so imports cannot resolve to another
folder's conftest): the pinned model id, a scripted fake provider and request/response builders."""
from dataclasses import dataclass, field

from nacre.core.model_provider import Message, ModelParams, ModelRequest, ModelResponse, ProviderError, Usage

MODEL = "gpt-4o-mini-2024-07-18"


@dataclass
class FakeProvider:
    script: list = field(default_factory=list)       # ModelResponse or ProviderError per call
    name: str = "openai"
    replay: bool = False
    calls: list = field(default_factory=list)

    def complete(self, request, *, timeout_s):
        self.calls.append((request, timeout_s))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def ok(text="the lesson", inp=1000, out=500, rid="resp_1"):
    return ModelResponse(text=text, finish_reason="stop", usage=Usage(inp, out, None), response_id=rid,
                         model_reported=MODEL, latency_ms=12)


def transient():
    return ProviderError("rate limited", retryable=True, error_class="rate_limit")


def req(content="Summarise the correction.", model=MODEL, provider="openai"):
    return ModelRequest(provider=provider, model=model, messages=(Message("user", content),),
                        params=ModelParams(max_tokens=256), purpose="test.seat")
