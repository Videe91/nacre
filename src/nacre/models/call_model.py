"""
Functionality: Call a model end to end: policy, scope and price checks, the call with bounded retries, and a
  ledger recording of every attempt.
Owns: the dated-pin check, the one-scope / readable-sources check (SI-1), the provider policy check (default deny),
  the fail-closed price lookup and cost, the retry loop, the `result` recording of each attempt (D-0022), and the
  replay short-circuit.
Public entry: call_model(), ModelCall, ModelCallRefused, load_prices(), DEFAULT_POLICY
Decisions: D-0021, D-0022, D-0005, D-0016
Assumptions: A-0025
Notes: The ONLY path from Nacre to a model provider (D-0021). Every attempt, failed or not, becomes one `result`
  event in the stream the prompt came from: actor_kind=model, actor_model = the requested dated pin,
  actor_model_version = the provider-reported model, source=system, authorship=external (so trust=untrusted:
  model output is content, never an instruction), cycle_id = run_id. The body records (D-0021 amendment 1):
  canonical request + sha256, response, status, attempt, timeout/retry settings, token usage and USD cost with the
  price-table version. Prompts carry user content; they are encrypted and shredded with the scope (D-0022, D3).
  D1: if strip_secrets would change any request or response string, the stored copy differs from what was sent, so
  the event is marked `redacted: true` and RecordedProvider will not replay it.
  D1: bodies over 1 MiB are refused (MAX_BODY_BYTES); the D-0022 attachment route for large bodies is not built
  yet (tracked in CURRENT.md). Phase 2 prompts are about 4 KB.
  Provider failures are RETURNED (ModelCall.error), not raised: raising inside the caller's scoped transaction
  would roll back the very recordings of the failed attempts. Callers commit, then call raise_for_error().
  Refusals (ModelCallRefused) are raised: nothing was sent, so there is nothing to record.
  Replay providers (`replay = True`) are answered without a new recording: replay never re-records. The scope,
  policy and price checks still run, so a replay is refused wherever the live call would have been.
  D1: recordings are appended inside the CALLER's transaction. A caller that must keep recordings even if its own
  work later fails (the sleep pass: paid calls must survive a crash, D-0020) commits per call.
"""
import json
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from nacre.core.encode_cbor import encode_cbor
from nacre.core.event import ActorKind, EventType, PayloadType, Source
from nacre.core.model_provider import (CallPolicy, ModelProvider, ModelRequest, ModelResponse, ProviderError,
                                       canonical_request, is_dated_pin, request_sha256)
from nacre.core.root_key_provider import RootKeyProvider
from nacre.ledger.append_event import AppendRequest, Authorship, append_event
from nacre.ledger.strip_secrets import strip_secrets
from nacre.models.set_model_policy import allowed_models
from nacre.scopes.open_scoped_session import ScopedSession

DEFAULT_POLICY = CallPolicy()
MAX_BODY_BYTES = 1024 * 1024
_PRICES = Path(__file__).parent / "data" / "prices.json"
_MODEL_ACTOR_NS = uuid.UUID("5d1f6c3e-8a5b-4e0e-9b7c-0d7a3b1e2f40")


class ModelCallRefused(PermissionError):
    """The call is not allowed (scope, policy, pin or price); nothing was sent to a provider."""


@dataclass(frozen=True)
class ModelCall:
    response: ModelResponse | None  # None when every attempt failed (see `error`)
    event_id: UUID | None           # the last recording (None only for replays, which never re-record)
    request_sha256: str
    attempts: int
    cost_usd: Decimal
    error: ProviderError | None = None

    def raise_for_error(self) -> "ModelCall":
        """Raise the provider error, if any. Call it AFTER the session commits, so failed attempts stay recorded."""
        if self.error is not None:
            raise self.error
        return self


def load_prices(path: Path = _PRICES) -> dict:
    return json.loads(Path(path).read_text())


def _cost(prices: dict, request: ModelRequest, usage) -> Decimal:
    p = prices["models"][request.model]
    million = Decimal(1_000_000)
    cached = usage.cached_input_tokens or 0
    return ((Decimal(usage.input_tokens - cached) * Decimal(p["input"]) + Decimal(cached) * Decimal(p["cached_input"])
             + Decimal(usage.output_tokens) * Decimal(p["output"])) / million).quantize(Decimal("0.0000000001"))


def _source_stream(session: ScopedSession, source_event_ids: list[UUID]) -> tuple[UUID, UUID]:
    ids = sorted(set(source_event_ids))
    if not ids:
        raise ModelCallRefused("a model request must name the events its content came from (SI-1)")
    rows = session.conn.execute("SELECT event_id, stream_id FROM ledger.events WHERE event_id = ANY(%s)",
                                (ids,)).fetchall()
    if len(rows) != len(ids):
        raise ModelCallRefused("some source events are not readable in this session (SI-1)")
    streams = {r[1] for r in rows}
    if len(streams) != 1:
        raise ModelCallRefused("one prompt, one scope: source events span several streams (D-0022)")
    stream = streams.pop()
    org = session.conn.execute("SELECT org_id FROM scopes.scopes WHERE stream_id = %s", (stream,)).fetchone()
    if org is None:
        raise ModelCallRefused("the source stream is not a registered scope")
    return stream, org[0]


def _strings_change(value) -> bool:
    if type(value) is str:
        return bool(strip_secrets(value).findings)
    if type(value) is list:
        return any(_strings_change(v) for v in value)
    if type(value) is dict:
        return any(_strings_change(v) for v in value.values())
    return False


def call_model(session: ScopedSession, key_provider: RootKeyProvider, model_provider: ModelProvider,
               request: ModelRequest, *, source_event_ids: list[UUID], run_id: UUID,
               policy: CallPolicy = DEFAULT_POLICY, prices: dict | None = None, sleep=time.sleep) -> ModelCall:
    """Send `request` (built only from `source_event_ids`) and record every attempt in their stream."""
    prices = load_prices() if prices is None else prices
    if not is_dated_pin(request.model):
        raise ModelCallRefused(f"model must be a dated pin, not {request.model!r} (D-0016 amendment 4)")
    if request.model not in prices["models"] or prices["models"][request.model]["provider"] != request.provider:
        raise ModelCallRefused(f"no price entry for {request.provider}/{request.model}: fail closed (D-0021 amendment 1)")
    if not model_provider.replay and model_provider.name != request.provider:
        raise ModelCallRefused(f"request is for {request.provider}, provider is {model_provider.name}")
    if policy.max_attempts < 1 or policy.timeout_s <= 0 or policy.backoff_s < 0:
        raise ModelCallRefused("invalid call policy")
    stream, org = _source_stream(session, source_event_ids)
    if (request.provider, request.model) not in allowed_models(session, key_provider, org):
        raise ModelCallRefused(f"org policy does not allow {request.provider}/{request.model} (default deny, D-0021)")
    digest = request_sha256(request)
    if model_provider.replay:
        response = model_provider.complete(request, timeout_s=policy.timeout_s)
        return ModelCall(response, None, digest, 1, _cost(prices, request, response.usage))

    canonical = canonical_request(request)
    settings = {"timeout_s": repr(policy.timeout_s), "max_attempts": policy.max_attempts, "backoff_s": repr(policy.backoff_s)}
    for attempt in range(1, policy.max_attempts + 1):
        started = time.monotonic()
        try:
            response = model_provider.complete(request, timeout_s=policy.timeout_s)
            error = None
        except ProviderError as exc:
            response, error = None, exc
        body = {"kind": "model_call", "purpose": request.purpose, "request": canonical, "request_sha256": digest,
                "attempt": attempt, "call_policy": settings, "run_id": str(run_id), "price_table": prices["version"]}
        if response is not None:
            cost = _cost(prices, request, response.usage)
            body.update(status="ok", latency_ms=response.latency_ms, cost_usd=str(cost), response={
                "text": response.text, "finish_reason": response.finish_reason, "response_id": response.response_id,
                "model_reported": response.model_reported,
                "usage": {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens,
                          "cached_input_tokens": response.usage.cached_input_tokens}})
        else:
            body.update(status="error", error_class=error.error_class, retryable=error.retryable,
                        latency_ms=int((time.monotonic() - started) * 1000), cost_usd="0")
        body["redacted"] = _strings_change(canonical) or (response is not None and _strings_change(response.text))
        if len(encode_cbor(body)) > MAX_BODY_BYTES:
            raise ModelCallRefused("model-call body over 1 MiB; the attachment route is not built yet (D-0022)")
        env = append_event(session, key_provider, AppendRequest(
            stream_id=stream, org_id=org, event_type=EventType.RESULT, payload_type=PayloadType.TRACE,
            actor_kind=ActorKind.MODEL, actor_id=uuid.uuid5(_MODEL_ACTOR_NS, f"{request.provider}/{request.model}"),
            source=Source.SYSTEM, authorship=Authorship.EXTERNAL, idempotency_key=str(uuid.uuid4()),
            content=body, actor_model=request.model,
            actor_model_version=response.model_reported if response is not None else None, cycle_id=run_id)).envelope
        if response is not None:
            return ModelCall(response, env.event_id, digest, attempt, cost)
        if not error.retryable or attempt == policy.max_attempts:
            return ModelCall(None, env.event_id, digest, attempt, Decimal(0), error)
        sleep(policy.backoff_s * attempt)
    raise AssertionError("unreachable")
