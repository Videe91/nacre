"""
Functionality: The model-call types and the ModelProvider protocol (types only, no logic beyond canonical form).
Owns: ModelRequest / ModelResponse / Usage / CallPolicy, the canonical (hashable) form of a request, and the
  provider error type.
Public entry: ModelRequest, Message, ModelParams, ModelResponse, Usage, CallPolicy, ModelProvider, ProviderError,
  canonical_request(), request_sha256(), is_dated_pin(), is_frame_id()
Decisions: D-0021, D-0022, D-0008
Assumptions: A-0025
Notes: Every parameter that reaches the provider is in the canonical form, and unset ones are explicit None
  (provider default, recorded as such). D1: floats (temperature, top_p) are encoded as their Python repr string
  because the deterministic CBOR subset has no floats (D-0008); repr round-trips exactly.
  The canonical form is versioned ("v": 1); changing it is a new version, never an edit, because recordings are
  keyed by its hash.
  `frame_id` (D-0022 amendments 1-2, 2026-10-02): a request may name its ContextFrame (the sha256 hex of the frame's
  canonical CBOR, D-0025). It is metadata: recorded on the call's result event, never sent to a provider, and NOT
  part of the canonical request, so the canonical form stays version 1 and the request hash depends only on what is
  sent. (Frame ids change across fresh databases while the prompt bytes do not, so a hash over the frame id would
  break recorded replay; amendment 2.)
  ProviderError carries the billing of a failed attempt (D-0021 amendment 2, owner 2026-10-02), with backwards
  compatible keyword defaults: `billed` (the provider may have charged for this attempt) and `usage` (the provider's
  reported usage, when it returned any). billed=False: nothing reached the provider (cost 0); billed=True with
  usage: costed from that usage; billed=True without usage: costed at the worst case (models/call_model.py).
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
    frame_id: str | None = None   # the ContextFrame the memory section came from (D-0022 am. 1); never sent


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

    def __init__(self, message: str, *, retryable: bool, error_class: str, billed: bool = False,
                 usage: "Usage | None" = None):
        super().__init__(message)
        self.retryable = retryable
        self.error_class = error_class
        self.billed = billed or usage is not None      # reported usage means the provider counted (and bills) it
        self.usage = usage


class ModelProvider(Protocol):
    name: str
    replay: bool         # True only for providers that answer from recordings (never record again)

    def complete(self, request: ModelRequest, *, timeout_s: float) -> ModelResponse: ...


def _f(x: float | None) -> str | None:
    return None if x is None else repr(float(x))


_FRAME_ID = re.compile(r"^[0-9a-f]{64}$")


def is_frame_id(value) -> bool:
    """True for a ContextFrame id: lowercase sha256 hex (D-0025)."""
    return type(value) is str and bool(_FRAME_ID.match(value))


def canonical_request(r: ModelRequest) -> dict:
    """The exact, hashable description of what is sent (version 1). frame_id is validated but never included."""
    out = {"v": 1, "provider": r.provider, "model": r.model, "purpose": r.purpose, "system": r.system,
           "messages": [{"role": m.role, "content": m.content} for m in r.messages],
           "params": {"max_tokens": r.params.max_tokens, "temperature": _f(r.params.temperature),
                      "top_p": _f(r.params.top_p), "seed": r.params.seed,
                      "response_format": r.params.response_format}}
    if r.frame_id is not None:
        if not is_frame_id(r.frame_id):
            raise ValueError("frame_id must be a lowercase sha256 hex digest (D-0022 amendment 1)")
    return out


def request_sha256(r: ModelRequest) -> str:
    return hashlib.sha256(encode_cbor(canonical_request(r))).hexdigest()


_DATED = re.compile(r"^[a-z0-9][a-z0-9.\-]*-(\d{4}-\d{2}-\d{2}|\d{8})$")


def is_dated_pin(model: str) -> bool:
    """True for a dated model version (e.g. gpt-4o-mini-2024-07-18, claude-x-20250929); owner rule: always pin."""
    return isinstance(model, str) and bool(_DATED.match(model))
