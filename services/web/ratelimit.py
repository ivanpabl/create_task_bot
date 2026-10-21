import time
from collections import deque


class TokenBucket:
    """Per-connection limiter: `rate` tokens/sec refill, up to `burst` stored."""

    def __init__(self, rate: float, burst: int):
        self.rate = rate
        self.burst = burst
        self.tokens = float(burst)
        self.updated = time.monotonic()

    def allow(self, cost: float = 1.0) -> bool:
        now = time.monotonic()
        self.tokens = min(self.burst, self.tokens + (now - self.updated) * self.rate)
        self.updated = now
        if self.tokens >= cost:
            self.tokens -= cost
            return True
        return False


class SlidingWindowLimiter:
    """Counts events per key within `window` seconds (in-memory, single process)."""

    def __init__(self, limit: int, window: float, max_keys: int = 10_000):
        self.limit = limit
        self.window = window
        self.max_keys = max_keys
        self._events: dict[str, deque[float]] = {}

    def _prune(self, key: str, now: float) -> deque[float]:
        events = self._events.get(key)
        if events is None:
            if len(self._events) >= self.max_keys:
                self._gc(now)
            events = self._events.setdefault(key, deque())
        while events and events[0] <= now - self.window:
            events.popleft()
        return events

    def _gc(self, now: float):
        for k in [k for k, ev in self._events.items() if not ev or ev[-1] <= now - self.window]:
            del self._events[k]

    def is_limited(self, key: str) -> bool:
        return len(self._prune(key, time.monotonic())) >= self.limit

    def hit(self, key: str):
        now = time.monotonic()
        self._prune(key, now).append(now)
