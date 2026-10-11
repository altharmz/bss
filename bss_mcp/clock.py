"""The only place bss_mcp reads time, so expiry and rate limits are testable."""

import time


class Clock:
    def now(self) -> float:
        """Wall clock, epoch seconds: token creation and expiry."""
        return time.time()

    def monotonic(self) -> float:
        """Rate limiting."""
        return time.monotonic()


class FixedClock(Clock):
    """Tests: both clocks stand still until moved."""

    def __init__(self, now: float = 1_800_000_000.0) -> None:
        self._now = now

    def now(self) -> float:
        return self._now

    def monotonic(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds
