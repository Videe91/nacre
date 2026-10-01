"""Sleep-pass test helpers (uniquely named module): a scripted fake provider and proposition JSON builders."""
import json
from dataclasses import dataclass, field

from nacre.core.model_provider import ModelResponse, ProviderError, Usage

MODEL = "gpt-4o-mini-2024-07-18"


@dataclass
class Fake:
    script: list = field(default_factory=list)       # str (JSON text) | Exception, one per live call
    name: str = "openai"
    replay: bool = False
    requests: list = field(default_factory=list)

    def complete(self, request, *, timeout_s):
        self.requests.append(request)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return ModelResponse(text=item, finish_reason="completed", usage=Usage(1000, 200, None), response_id="r",
                             model_reported=MODEL, latency_ms=5)


def props(*items):
    """items: (section, quote, nucleus[, qualifiers])"""
    return json.dumps({"propositions": [{"section": i[0], "quote": i[1], "nucleus": i[2],
                                         "qualifiers": [{"type": t, "text": x} for t, x in (i[3] if len(i) > 3 else [])]}
                                        for i in items]})


def crash():
    return ProviderError("connection reset", retryable=False, error_class="APIConnectionError")
