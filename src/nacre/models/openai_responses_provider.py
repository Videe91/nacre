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
        started = time.monotonic()
        try:
            r = self._sdk().responses.create(**kwargs)
        except (openai.APITimeoutError, openai.APIConnectionError, openai.RateLimitError, openai.InternalServerError) as exc:
            raise ProviderError(f"openai {type(exc).__name__}", retryable=True, error_class=type(exc).__name__) from None
        except openai.APIStatusError as exc:
            raise ProviderError(f"openai {type(exc).__name__} (HTTP {exc.status_code})", retryable=exc.status_code >= 500,
                                error_class=type(exc).__name__) from None
        u = r.usage
        details = getattr(u, "input_tokens_details", None)
        incomplete = getattr(r, "incomplete_details", None)
        finish = r.status if incomplete is None else f"{r.status}:{getattr(incomplete, 'reason', None)}"
        return ModelResponse(text=r.output_text, finish_reason=finish,
                             usage=Usage(u.input_tokens, u.output_tokens, getattr(details, "cached_tokens", None)),
                             response_id=r.id, model_reported=r.model,
                             latency_ms=int((time.monotonic() - started) * 1000))
