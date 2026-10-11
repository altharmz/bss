"""Token bucket per key (a principal, or a client IP on the auth endpoints), in memory:
one process, and a restart only refills the buckets."""

from bss_mcp.clock import Clock


class RateLimit:
    def __init__(self, clock: Clock, per_minute: int) -> None:
        self.clock = clock
        self.capacity = float(per_minute)
        self.rate = per_minute / 60.0
        self._buckets: dict[str, tuple[float, float]] = {}  # key -> (tokens, at)

    def take(self, key: str) -> float | None:
        """None if allowed (one token taken); otherwise the seconds until the next one."""
        now = self.clock.monotonic()
        tokens, at = self._buckets.get(key, (self.capacity, now))
        tokens = min(self.capacity, tokens + (now - at) * self.rate)
        if tokens >= 1:
            self._buckets[key] = (tokens - 1, now)
            return None
        self._buckets[key] = (tokens, now)
        return (1 - tokens) / self.rate
