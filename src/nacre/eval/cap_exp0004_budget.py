"""
Functionality: Enforce the EXP-0004 hard budget cap on every live model call of a full run, failing closed.
Owns: the cap constant, the worst-case cost of a request before it is sent, the running spend, and the abort error.
Public entry: CappedProvider, BudgetExceeded, worst_case_cost(), HARD_CAP_USD
Decisions: D-0021, D-0016
Assumptions: none
Notes: EVALUATION HARNESS ONLY. EXP-0004 "Cost estimate": hard budget cap $15 per full run (k = 3), failing closed
  (D-0021); hitting the cap is an infrastructure abort (recorded; the run is repeated in full with a new id, never
  resumed to reach a pass).
  D1 (flagged to the owner as the enforcement reading of "failing closed"):
  - one CappedProvider wraps the live provider for the WHOLE run (all k replicates, sleep seats and transfers alike);
  - before each call, the request's worst-case cost is reserved: input tokens <= UTF-8 bytes of every message and the
    system prompt + 16 per message (a BPE token is at least one byte), all at the uncached input price, plus
    max_tokens at the output price. If spent + worst case > cap, nothing is sent and BudgetExceeded is raised;
  - after a response, the actual cost (from the reported usage, the call_model price formula) is added; after a
    provider error the worst case is added (the provider may have billed a timed-out call);
  - prices come from the same dated table call_model uses (models/data/prices.json); a model with no entry fails
    closed there first.
  Replay providers are never wrapped: a replay sends nothing.
"""
from decimal import Decimal

from nacre.core.model_provider import ModelProvider, ModelRequest, ModelResponse, ProviderError
from nacre.models.call_model import load_prices

HARD_CAP_USD = Decimal("15")
_MILLION = Decimal(1_000_000)
_PER_MESSAGE_OVERHEAD = 16


class BudgetExceeded(RuntimeError):
    """The next call could exceed the hard cap: nothing was sent (an infrastructure abort)."""


def worst_case_cost(request: ModelRequest, prices: dict) -> Decimal:
    p = prices["models"][request.model]
    texts = [m.content for m in request.messages] + ([request.system] if request.system else [])
    tokens_in = sum(len(t.encode()) for t in texts) + _PER_MESSAGE_OVERHEAD * (len(texts) + 1)
    return (Decimal(tokens_in) * Decimal(p["input"]) + Decimal(request.params.max_tokens) * Decimal(p["output"])) / _MILLION


class CappedProvider:
    replay = False

    def __init__(self, inner: ModelProvider, cap_usd: Decimal = HARD_CAP_USD, prices: dict | None = None):
        if inner.replay:
            raise ValueError("a replay provider sends nothing and is never capped")
        self.name, self._inner, self.cap = inner.name, inner, Decimal(cap_usd)
        self._prices = load_prices() if prices is None else prices
        self.spent, self.calls, self.tripped = Decimal(0), 0, False

    def complete(self, request: ModelRequest, *, timeout_s: float) -> ModelResponse:
        worst = worst_case_cost(request, self._prices)
        if self.spent + worst > self.cap:
            self.tripped = True
            raise BudgetExceeded(f"budget cap ${self.cap}: spent ${self.spent}, next call up to ${worst}")
        self.calls += 1
        try:
            response = self._inner.complete(request, timeout_s=timeout_s)
        except ProviderError:
            self.spent += worst
            raise
        p, u = self._prices["models"][request.model], response.usage
        cached = u.cached_input_tokens or 0
        self.spent += ((Decimal(u.input_tokens - cached) * Decimal(p["input"]) + Decimal(cached) * Decimal(p["cached_input"])
                        + Decimal(u.output_tokens) * Decimal(p["output"])) / _MILLION)
        return response
