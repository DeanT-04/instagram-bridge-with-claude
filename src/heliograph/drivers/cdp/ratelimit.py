"""Client-side rate limiting with jitter (separate budgets for reads and writes)."""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from typing import Literal

from heliograph.config import Settings, get_settings
from heliograph.eye import event

__all__ = ["RateLimiter", "limiter_for"]

Kind = Literal["read", "write"]


class RateLimiter:
    """Enforce ``min_interval + U(0, jitter)`` seconds between successive acquisitions.

    Args:
        min_interval: Minimum spacing in seconds.
        jitter: Maximum random extra seconds added per call.
        clock/sleep/rand: Injectable for tests.
    """

    def __init__(
        self,
        min_interval: float,
        jitter: float,
        *,
        name: str = "limiter",
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rand: Callable[[], float] = random.random,
    ) -> None:
        self.min_interval = min_interval
        self.jitter = jitter
        self.name = name
        self._clock = clock
        self._sleep = sleep
        self._rand = rand
        self._last: float | None = None
        self._lock = asyncio.Lock()

    async def acquire(self) -> float:
        """Wait until the next call is allowed; returns the seconds waited."""
        async with self._lock:
            waited = 0.0
            if self._last is not None:
                gap = self.min_interval + self.jitter * self._rand()
                waited = max(0.0, self._last + gap - self._clock())
                if waited > 0:
                    event("ratelimit.wait", limiter=self.name, seconds=round(waited, 3))
                    await self._sleep(waited)
            self._last = self._clock()
            return waited


_REGISTRY: dict[tuple[str, Kind], RateLimiter] = {}


def limiter_for(
    kind: Kind, account: str = "default", settings: Settings | None = None
) -> RateLimiter:
    """Return the process-wide limiter for ``(account, kind)``, created from settings."""
    key = (account, kind)
    if key not in _REGISTRY:
        s = settings or get_settings()
        if kind == "read":
            _REGISTRY[key] = RateLimiter(s.read_min_interval, s.read_jitter, name=f"{account}.read")
        else:
            _REGISTRY[key] = RateLimiter(s.write_min_interval, s.write_jitter,
                                         name=f"{account}.write")
    return _REGISTRY[key]
