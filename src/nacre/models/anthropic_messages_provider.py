"""
Functionality: Send one ModelRequest to Anthropic's Messages API and return a ModelResponse.
Owns: the request mapping, the capability check before any network call, the SDK client (its own retries OFF;
  call_model owns retries), the per-call timeout, the error classification (retryable or not), and the stop-reason
  and usage mapping.
Public entry: AnthropicMessagesProvider, PINNED_MODELS
Decisions: D-0028, D-0021
Assumptions: A-0024, A-0043
Notes: The only file that imports `anthropic` (D-0021; checked by scripts/check_structure.py, SI-4). The API key is
  read by the SDK from ANTHROPIC_API_KEY. This file never reads, stores or logs it, and error messages carry only the
  exception class and HTTP status, never the SDK's message text, so nothing the SDK echoes can leak (SI-2).
  D-0028 owner decision 1: the Claude seat is `claude-haiku-4-5-20251001` only (PINNED_MODELS); any other model is
  refused before the network. No server-side fallbacks are ever requested (owner decision 2): the plain
  `messages.create` is used, never the beta fallback form.
  D1 local choices:
  - Capabilities come from the model's `capabilities` row in models/data/prices.json (D-0028 §2). A model without
    that row is refused (fail closed). `temperature` set on a model whose row says false, or a JSON schema on a model
    whose row says false, is a non-retryable ProviderError before any network call, never a silent drop.
  - `top_p` and `seed` are refused (non-retryable), like OpenAI's `seed`: D-0028 maps only temperature.
  - The anthropic 1.x SDK has no `temperature` keyword on `messages.create` (removed from the signature, not from
    the API); it is sent through `extra_body={"temperature": t}`, which the SDK merges into the request JSON as-is.
  - JSON schema: the request's response_format is the OpenAI-shaped {"type": "json_schema", "name", "strict",
    "schema"}. It is sent as output_config.format = {"type": "json_schema", "schema": <schema>} (never the deprecated
    output_format). `name` is an OpenAI label and is not sent; `strict: false` is refused, because Anthropic's
    structured output is always constrained, so sending it would change what the request means.
  - Stop reasons: end_turn returns a response (finish_reason "end_turn", the provider's own value, recorded as is);
    max_tokens -> ProviderError error_class "truncated", non-retryable; refusal -> error_class
    "refused:<stop_details.category or none>", non-retryable (the category is thereby recorded by call_model);
    any other stop reason -> "unexpected_stop:<reason>", non-retryable.
  - Any content block other than text (e.g. a fallback or thinking block) is an error: this adapter asked for text.
  - Usage: input_tokens = input + cache_read + cache_creation (the total, as for OpenAI); cached_input_tokens =
    cache_read (priced at the table's `cached_input`, the cache-read price). The Usage type has no cache-write field,
    so a response reporting cache_creation_input_tokens > 0 cannot be costed truthfully and is a non-retryable
    "unpriced_usage" error. It should never happen: this adapter never sends cache_control.
  - Billing (D-0021 amendment 2, owner 2026-10-02): every error raised AFTER a response came back (truncated,
    refused, unexpected stop or content, unpriced or missing usage) is `billed=True` and carries the response's usage
    when it is complete and priceable (else None: call_model records the worst case). An APITimeoutError is billed
    without usage (the request may have been sent and charged: worst case). Any other APIConnectionError, an HTTP
    status error, a client that could not be built and every pre-network refusal are `billed=False` (cost 0). Any
    other AnthropicError raised by `messages.create` (e.g. a response that failed the SDK's validation) is billed
    without usage, because it may have come after the provider answered. D1, flagged: a connection dropped after the
    request was fully sent is indistinguishable here from one that never connected and is recorded unbilled.
"""
import re
import time

from nacre.core.model_provider import ModelRequest, ModelResponse, ProviderError, Usage

PINNED_MODELS = frozenset({"claude-haiku-4-5-20251001"})     # D-0028 owner decision 1 (option a)
_SAFE = re.compile(r"^[a-z0-9_\-]{1,64}$")


def _refuse(message: str, error_class: str) -> ProviderError:
    return ProviderError(message, retryable=False, error_class=error_class)


def _billed(message: str, error_class: str, usage: Usage | None) -> ProviderError:
    return ProviderError(message, retryable=False, error_class=error_class, billed=True, usage=usage)


def _usage(r) -> Usage | None:
    """The response's usage as a priceable Usage, or None when it is missing, malformed or has cache writes."""
    u = getattr(r, "usage", None)
    vals = [getattr(u, k, None) for k in ("input_tokens", "output_tokens")]
    vals += [getattr(u, k, None) or 0 for k in ("cache_read_input_tokens", "cache_creation_input_tokens")]
    if u is None or not all(type(v) is int and v >= 0 for v in vals) or vals[3]:
        return None
    return Usage(vals[0] + vals[2], vals[1], vals[2])


def _label(value) -> str:
    """A provider-supplied enum value, safe to record (anything unexpected becomes 'other')."""
    if value is None:
        return "none"
    return value if isinstance(value, str) and _SAFE.match(value) else "other"


class AnthropicMessagesProvider:
    name = "anthropic"
    replay = False

    def __init__(self, client=None, prices: dict | None = None):
        self._client = client
        self._prices = prices

    def _sdk(self):
        if self._client is None:
            from anthropic import Anthropic
            self._client = Anthropic(max_retries=0)
        return self._client

    def _capabilities(self, model: str) -> dict:
        if self._prices is None:
            from nacre.models.call_model import load_prices
            self._prices = load_prices()
        entry = self._prices.get("models", {}).get(model)
        caps = entry.get("capabilities") if isinstance(entry, dict) and entry.get("provider") == "anthropic" else None
        if not isinstance(caps, dict):
            raise _refuse(f"no anthropic capabilities row for {model}: fail closed (D-0028)", "unsupported_model")
        return caps

    def _kwargs(self, request: ModelRequest, timeout_s: float) -> dict:
        if request.provider != "anthropic":
            raise _refuse("request is not for anthropic", "wrong_provider")
        if request.model not in PINNED_MODELS:
            raise _refuse(f"model {request.model} is not the pinned Claude seat (D-0028)", "unsupported_model")
        caps = self._capabilities(request.model)
        p = request.params
        if p.seed is not None:
            raise _refuse("seed is not supported by the Messages API", "unsupported_param")
        if p.top_p is not None:
            raise _refuse("top_p is not mapped for anthropic (D-0028)", "unsupported_param")
        kwargs = {"model": request.model, "max_tokens": p.max_tokens,
                  "messages": [{"role": m.role, "content": m.content} for m in request.messages], "timeout": timeout_s}
        if request.system is not None:
            kwargs["system"] = request.system
        if p.temperature is not None:
            if caps.get("temperature") is not True:
                raise _refuse(f"{request.model} does not accept temperature", "unsupported_param")
            kwargs["extra_body"] = {"temperature": p.temperature}
        if p.response_format is not None:
            rf = p.response_format
            if caps.get("json_schema") is not True:
                raise _refuse(f"{request.model} does not accept a JSON schema", "unsupported_param")
            if rf.get("type") != "json_schema" or not isinstance(rf.get("schema"), dict) or rf.get("strict") is False:
                raise _refuse("response_format must be a strict json_schema with a schema", "unsupported_param")
            kwargs["output_config"] = {"format": {"type": "json_schema", "schema": rf["schema"]}}
        return kwargs

    def complete(self, request: ModelRequest, *, timeout_s: float) -> ModelResponse:
        kwargs = self._kwargs(request, timeout_s)                # every refusal happens before the network
        import anthropic
        try:
            client = self._sdk()
        except anthropic.AnthropicError as exc:                  # nothing was sent: not billed
            raise _refuse(f"anthropic {type(exc).__name__}", type(exc).__name__) from None
        started = time.monotonic()
        try:
            r = client.messages.create(**kwargs)
        except anthropic.APITimeoutError as exc:                 # possibly sent and charged: worst case
            raise ProviderError(f"anthropic {type(exc).__name__}", retryable=True, error_class=type(exc).__name__,
                                billed=True) from None
        except anthropic.APIConnectionError as exc:
            raise ProviderError(f"anthropic {type(exc).__name__}", retryable=True,
                                error_class=type(exc).__name__) from None
        except anthropic.APIStatusError as exc:
            code = exc.status_code
            raise ProviderError(f"anthropic {type(exc).__name__} (HTTP {code})", retryable=code == 429 or code >= 500,
                                error_class=type(exc).__name__) from None
        except anthropic.AnthropicError as exc:
            raise _billed(f"anthropic {type(exc).__name__}", type(exc).__name__, None) from None
        latency = int((time.monotonic() - started) * 1000)
        usage = _usage(r)
        try:
            return self._response(r, usage, latency)
        except ProviderError:
            raise
        except Exception:                                        # a malformed response is still a billed one
            raise _billed("anthropic response could not be mapped", "unexpected_response", usage) from None

    @staticmethod
    def _response(r, usage: Usage | None, latency: int) -> ModelResponse:
        if r.stop_reason == "max_tokens":
            raise _billed("anthropic response truncated at max_tokens", "truncated", usage)
        if r.stop_reason == "refusal":
            category = _label(getattr(getattr(r, "stop_details", None), "category", None))
            raise _billed(f"anthropic refusal ({category})", f"refused:{category}", usage)
        if r.stop_reason != "end_turn":
            raise _billed("anthropic stopped unexpectedly", f"unexpected_stop:{_label(r.stop_reason)}", usage)
        kinds = {getattr(b, "type", None) for b in r.content}
        if kinds - {"text"}:
            raise _billed("anthropic returned non-text content", "unexpected_content", usage)
        if getattr(getattr(r, "usage", None), "cache_creation_input_tokens", None):
            raise _billed("anthropic reported cache-write tokens, which Usage cannot price", "unpriced_usage", None)
        if usage is None:
            raise _billed("anthropic response has no usable usage", "missing_usage", None)
        return ModelResponse(text="".join(b.text for b in r.content), finish_reason=r.stop_reason, usage=usage,
                             response_id=r.id, model_reported=r.model, latency_ms=latency)
