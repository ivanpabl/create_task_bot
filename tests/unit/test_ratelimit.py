"""Rate limiters with a controllable clock."""
import pytest

from services.web import ratelimit
from services.web.ratelimit import SlidingWindowLimiter, TokenBucket


@pytest.fixture
def clock(monkeypatch):
    class Clock:
        now = 1000.0

        def __call__(self):
            return self.now

        def advance(self, seconds):
            self.now += seconds

    c = Clock()
    monkeypatch.setattr(ratelimit.time, 'monotonic', c)
    return c


class TestTokenBucket:
    def test_burst_then_reject(self, clock):
        bucket = TokenBucket(rate=1, burst=3)
        assert [bucket.allow() for _ in range(4)] == [True, True, True, False]

    def test_refills_with_time(self, clock):
        bucket = TokenBucket(rate=2, burst=2)
        bucket.allow(); bucket.allow()
        assert not bucket.allow()
        clock.advance(0.5)                     # +1 token
        assert bucket.allow()
        assert not bucket.allow()

    def test_refill_is_capped_at_burst(self, clock):
        bucket = TokenBucket(rate=10, burst=2)
        clock.advance(3600)
        assert [bucket.allow() for _ in range(3)] == [True, True, False]

    def test_cost_larger_than_balance(self, clock):
        bucket = TokenBucket(rate=1, burst=5)
        assert not bucket.allow(cost=6)
        assert bucket.allow(cost=5)


class TestSlidingWindow:
    def test_limit_boundary(self, clock):
        limiter = SlidingWindowLimiter(limit=3, window=60)
        for _ in range(2):
            limiter.hit('ip')
        assert not limiter.is_limited('ip')    # 2 of 3
        limiter.hit('ip')
        assert limiter.is_limited('ip')        # 3 of 3

    def test_events_expire_exactly_at_window_end(self, clock):
        limiter = SlidingWindowLimiter(limit=1, window=60)
        limiter.hit('ip')
        clock.advance(59)
        assert limiter.is_limited('ip')
        clock.advance(1)
        assert not limiter.is_limited('ip')

    def test_keys_are_independent(self, clock):
        limiter = SlidingWindowLimiter(limit=1, window=60)
        limiter.hit('a')
        assert limiter.is_limited('a')
        assert not limiter.is_limited('b')

    def test_stale_keys_are_collected_when_full(self, clock):
        limiter = SlidingWindowLimiter(limit=1, window=60, max_keys=2)
        limiter.hit('a'); limiter.hit('b')
        clock.advance(61)
        limiter.hit('c')
        assert set(limiter._events) == {'c'}
