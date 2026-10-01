"""
Functionality: The model-call types and the ModelProvider protocol (types only, no logic beyond canonical form).
Owns: ModelRequest / ModelResponse / Usage / CallPolicy, the canonical (hashable) form of a request, and the
  provider error type.
Public entry: ModelRequest, Message, ModelParams, ModelResponse, Usage, CallPolicy, ModelProvider, ProviderError,
  canonical_request(), request_sha256(), is_dated_pin()
Decisions: D-0021, D-0022, D-0008
Assumptions: A-0025
Notes: Every parameter that reaches the provider is in the canonical form, and unset ones are explicit None
  (provider default, recorded as such). D1: floats (temperature, top_p) are encoded as their Python repr string
  because the deterministic CBOR subset has no floats (D-0008); repr round-trips exactly.
  The canonical form is versioned ("v": 1); changing it is a new version, never an edit, because recordings are
  keyed by its hash.
"""
import hashlib
import re
from dataclasses import dataclass, field
from typing import Protocol

from nacre.core.encode_cbor import encode_cbor


@dataclass(frozen=True)
class Message:
    role: str            # "user" | "assistant"
    content: str


@dataclass(frozen=True)
class ModelParams:
    max_tokens: int
    temperature: float | None = None
    top_p: float | None = None
    seed: int | None = None
    response_format: dict | None = None


@dataclass(frozen=True)
class ModelRequest:
    provider: str        # "openai" | "anthropic"
    model: str           # a dated pin, e.g. "gpt-4o-mini-2024-07-18" (D-0016 amendment 4)
    messages: tuple[Message, ...]
    params: ModelParams
    purpose: str         # the seat, e.g. "sleep.propose"
    system: str | None = None


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int | None = None


@dataclass(frozen=True)
class ModelResponse:
    text: str
    finish_reason: str | None
    usage: Usage
    response_id: str | None
    model_reported: str | None
    latency_ms: int


@dataclass(frozen=True)
class CallPolicy:
    """Timeout and retry settings, recorded on every call (D-0021 amendment 1)."""
    timeout_s: float = 60.0
    max_attempts: int = 3
    backoff_s: float = 2.0


class ProviderError(RuntimeError):
    """A provider call failed. `retryable` marks transient failures (timeouts, rate limits, 5xx)."""

    def __init__(self, message: str, *, retryable: bool, error_class: str):
        super().__init__(message)
        self.retryable = retryable
        self.error_class = error_class


class ModelProvider(Protocol):
    name: str
    replay: bool         # True only for providers that answer from recordings (never record again)

    def complete(self, request: ModelRequest, *, timeout_s: float) -> ModelResponse: ...


def _f(x: float | None) -> str | None:
    return None if x is None else repr(float(x))


def canonical_request(r: ModelRequest) -> dict:
    """The exact, hashable description of what is sent to the provider (version 1)."""
    return {"v": 1, "provider": r.provider, "model": r.model, "purpose": r.purpose, "system": r.system,
            "messages": [{"role": m.role, "content": m.content} for m in r.messages],
            "params": {"max_tokens": r.params.max_tokens, "temperature": _f(r.params.temperature),
                       "top_p": _f(r.params.top_p), "seed": r.params.seed,
                       "response_format": r.params.response_format}}


def request_sha256(r: ModelRequest) -> str:
    return hashlib.sha256(encode_cbor(canonical_request(r))).hexdigest()


_DATED = re.compile(r"^[a-z0-9][a-z0-9.\-]*-(\d{4}-\d{2}-\d{2}|\d{8})$")


def is_dated_pin(model: str) -> bool:
    """True for a dated model version (e.g. gpt-4o-mini-2024-07-18, claude-x-20250929); owner rule: always pin."""
    return isinstance(model, str) and bool(_DATED.match(model))
