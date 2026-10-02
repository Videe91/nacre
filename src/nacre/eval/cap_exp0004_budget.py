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
  - before each call, the request's worst case (models/call_model.worst_case_cost: an input-token upper bound from
    UTF-8 bytes of the messages, system prompt and JSON schema plus 16 per part, at the uncached input price, plus
    max_tokens at the output price) is reserved: if spent + worst case > cap, nothing is sent and BudgetExceeded is
    raised;
  - D-0021 amendment 2 (owner, 2026-10-02): the cap is enforced on the RECORDED costs. After each attempt, `spent`
    grows by exactly what call_model records for it (call_model.attempt_cost): the reported usage for a response or
    a billed failure that carries usage (refusal, truncation), the worst case for a billed failure without usage (a
    timeout), 0 for an unbilled one (a connection that never reached the provider). Any other exception escaping the
    inner provider is charged the worst case (call_model records nothing for it; conservative);
  - prices come from the same dated table call_model uses (models/data/prices.json); a model with no entry fails
    closed there first.
  Replay providers are never wrapped: a replay sends nothing.
"""
from decimal import Decimal

from nacre.core.model_provider import ModelProvider, ModelRequest, ModelResponse, ProviderError
from nacre.models.call_model import attempt_cost, load_prices
from nacre.models.call_model import worst_case_cost as _worst_case

HARD_CAP_USD = Decimal("15")


class BudgetExceeded(RuntimeError):
    """The next call could exceed the hard cap: nothing was sent (an infrastructure abort)."""


def worst_case_cost(request: ModelRequest, prices: dict) -> Decimal:
    """The most `request` can be billed (the same bound call_model records as `worst_case`)."""
    return _worst_case(prices, request)


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
        except ProviderError as exc:
            self.spent += attempt_cost(self._prices, request, error=exc)[0]
            raise
        except Exception:
            self.spent += worst
            raise
        self.spent += attempt_cost(self._prices, request, response=response)[0]
        return response
