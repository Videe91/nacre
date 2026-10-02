"""Tests for interface/limit_rate.py: per-principal token buckets (D-0026 §4; amendment 3 gap 4, proposed values)."""
import uuid

import pytest

from nacre.interface.limit_rate import BURST, RATE_PER_MINUTE, RateLimiter


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_a_burst_is_admitted_then_refused_with_the_wait_and_refills_at_the_rate():
    clock, p = Clock(), uuid.uuid4()
    rl = RateLimiter(60, 3, clock=clock)
    assert [rl.admit(p) for _ in range(3)] == [0.0, 0.0, 0.0]
    wait = rl.admit(p)
    assert wait == pytest.approx(1.0)                        # 60/min: one token per second
    clock.t += 0.5
    assert rl.admit(p) == pytest.approx(0.5)                 # a refused call does not consume
    clock.t += 0.5
    assert rl.admit(p) == 0.0
    clock.t += 1000
    assert [rl.admit(p) for _ in range(4)][-1] > 0           # refill is capped at the burst


def test_principals_have_separate_buckets_and_the_defaults_are_the_proposed_ones():
    clock = Clock()
    rl = RateLimiter(60, 1, clock=clock)
    a, b = uuid.uuid4(), uuid.uuid4()
    assert rl.admit(a) == 0.0 and rl.admit(a) > 0 and rl.admit(b) == 0.0
    assert (RATE_PER_MINUTE, BURST) == (120, 30)
    with pytest.raises(ValueError):
        RateLimiter(0, 1)
