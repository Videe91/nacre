"""
Functionality: Limit interface calls per principal: an in-process token bucket that admits a call or refuses it with
  the seconds until the next token.
Owns: the bucket arithmetic, the per-principal table and its default rate.
Public entry: RateLimiter, RATE_PER_MINUTE, BURST
Decisions: D-0026
Assumptions: none
Notes: D-0026 §4 ("per-principal rate limit") and amendment 3 gap 4 (PROPOSED: 120 calls per minute, bursts of 30,
  in-process). Built with those defaults behind these constants, pending the owner's ruling. A refusal is the
  interface's `rate_limited` error. Thread-safe: MCP runs sync tools in worker threads.
"""
import threading
import time
from uuid import UUID

RATE_PER_MINUTE = 120
BURST = 30


class RateLimiter:
    def __init__(self, rate_per_minute: int = RATE_PER_MINUTE, burst: int = BURST, clock=time.monotonic):
        if rate_per_minute <= 0 or burst <= 0:
            raise ValueError("rate and burst must be positive")
        self._rate, self._burst, self._clock = rate_per_minute / 60.0, float(burst), clock
        self._buckets: dict[UUID, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def admit(self, principal_id: UUID) -> float:
        """0.0 when the call is admitted; otherwise the seconds to wait (the call is not admitted)."""
        with self._lock:
            now = self._clock()
            tokens, last = self._buckets.get(principal_id, (self._burst, now))
            tokens = min(self._burst, tokens + (now - last) * self._rate)
            if tokens >= 1.0:
                self._buckets[principal_id] = (tokens - 1.0, now)
                return 0.0
            self._buckets[principal_id] = (tokens, now)
            return (1.0 - tokens) / self._rate
