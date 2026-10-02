"""
Functionality: Send one ModelRequest to OpenAI's Responses API and return a ModelResponse.
Owns: the request mapping, the SDK client (its own retries OFF; call_model owns retries), the per-call timeout, the
  error classification (retryable or not), and the usage and finish-reason mapping.
Public entry: OpenAIResponsesProvider
Decisions: D-0021
Assumptions: A-0024
Notes: The only file that imports `openai` (D-0021; checked by scripts/check_structure.py, SI-4). The API key is
  read by the SDK from OPENAI_API_KEY. This file never reads, stores or logs it, and error messages carry only the
  exception class and HTTP status, never the SDK's message text, so nothing the SDK echoes can leak (SI-2).
  D1: unsupported request features (seed) are refused as a non-retryable ProviderError rather than silently dropped,
  so a recording never claims a parameter that was not sent.
  Billing (D-0021 amendment 2, owner 2026-10-02): a returned response is costed from its usage whatever its status
  (an `incomplete` / truncated response is returned as a response, finish_reason "incomplete:<reason>", as before).
  A returned response whose usage is missing or malformed, or that cannot be mapped, is a non-retryable billed
  ProviderError (with the usage when it is readable, else None: call_model records the worst case). An
  APITimeoutError is billed without usage (possibly sent and charged: worst case). Any other connection error, an
  HTTP status error, a client that could not be built and every pre-network refusal are unbilled (cost 0). Any other
  OpenAIError from `responses.create` (e.g. a response that failed the SDK's validation) is billed without usage.
  D1, flagged: a connection dropped after the request was fully sent cannot be told apart here from one that never
  connected and is recorded unbilled.
"""
import time

from nacre.core.model_provider import ModelRequest, ModelResponse, ProviderError, Usage


class OpenAIResponsesProvider:
    name = "openai"
    replay = False

    def __init__(self, client=None):
        self._client = client

    def _sdk(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(max_retries=0)
        return self._client

    def complete(self, request: ModelRequest, *, timeout_s: float) -> ModelResponse:
        import openai
        if request.provider != "openai":
            raise ProviderError("request is not for openai", retryable=False, error_class="wrong_provider")
        if request.params.seed is not None:
            raise ProviderError("seed is not supported by the Responses API", retryable=False, error_class="unsupported_param")
        kwargs = {"model": request.model, "max_output_tokens": request.params.max_tokens,
                  "input": [{"role": m.role, "content": m.content} for m in request.messages], "timeout": timeout_s}
        if request.system is not None:
            kwargs["instructions"] = request.system
        if request.params.temperature is not None:
            kwargs["temperature"] = request.params.temperature
        if request.params.top_p is not None:
            kwargs["top_p"] = request.params.top_p
        if request.params.response_format is not None:
            kwargs["text"] = {"format": request.params.response_format}
        try:
            client = self._sdk()
        except openai.OpenAIError as exc:                        # nothing was sent: not billed
            raise ProviderError(f"openai {type(exc).__name__}", retryable=False, error_class=type(exc).__name__) from None
        started = time.monotonic()
        try:
            r = client.responses.create(**kwargs)
        except openai.APITimeoutError as exc:                    # possibly sent and charged: worst case
            raise ProviderError(f"openai {type(exc).__name__}", retryable=True, error_class=type(exc).__name__,
                                billed=True) from None
        except (openai.APIConnectionError, openai.RateLimitError, openai.InternalServerError) as exc:
            raise ProviderError(f"openai {type(exc).__name__}", retryable=True, error_class=type(exc).__name__) from None
        except openai.APIStatusError as exc:
            raise ProviderError(f"openai {type(exc).__name__} (HTTP {exc.status_code})", retryable=exc.status_code >= 500,
                                error_class=type(exc).__name__) from None
        except openai.OpenAIError as exc:
            raise ProviderError(f"openai {type(exc).__name__}", retryable=False, error_class=type(exc).__name__,
                                billed=True) from None
        latency = int((time.monotonic() - started) * 1000)
        usage = _usage(r)
        if usage is None:
            raise ProviderError("openai response has no usable usage", retryable=False, error_class="missing_usage",
                                billed=True)
        try:
            incomplete = getattr(r, "incomplete_details", None)
            finish = r.status if incomplete is None else f"{r.status}:{getattr(incomplete, 'reason', None)}"
            return ModelResponse(text=r.output_text, finish_reason=finish, usage=usage, response_id=r.id,
                                 model_reported=r.model, latency_ms=latency)
        except Exception:                                        # a malformed response is still a billed one
            raise ProviderError("openai response could not be mapped", retryable=False,
                                error_class="unexpected_response", usage=usage) from None


def _usage(r) -> Usage | None:
    """The response's usage, or None when it is missing or malformed."""
    u = getattr(r, "usage", None)
    inp, out = getattr(u, "input_tokens", None), getattr(u, "output_tokens", None)
    cached = getattr(getattr(u, "input_tokens_details", None), "cached_tokens", None)
    if u is None or not all(type(v) is int and v >= 0 for v in (inp, out)):
        return None
    if cached is not None and not (type(cached) is int and 0 <= cached <= inp):
        return None
    return Usage(inp, out, cached)
