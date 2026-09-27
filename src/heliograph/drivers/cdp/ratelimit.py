"""Client-side rate limiting with jitter (separate budgets for reads and writes)."""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from typing import Literal

from heliograph.config import Settings, get_settings
from heliograph.eye import event
from heliograph.writelimit import SharedWriteLimiter

__all__ = ["RateLimiter", "SharedRateLimiter", "limiter_for"]

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


class SharedRateLimiter(RateLimiter):
    """Write limiter backed by :class:`~heliograph.writelimit.SharedWriteLimiter`, so CDP
    writes are spaced against every Heliograph process (and UIA writes), not just this one."""

    def __init__(self, shared: SharedWriteLimiter, *, name: str = "write") -> None:
        super().__init__(0.0, 0.0, name=name)
        self.shared = shared

    async def acquire(self) -> float:
        """Reserve the next shared write slot and wait for it."""
        async with self._lock:
            return await self.shared.acquire()


_REGISTRY: dict[tuple[str, Kind], RateLimiter] = {}


def limiter_for(
    kind: Kind, account: str = "default", settings: Settings | None = None
) -> RateLimiter:
    """Return the process-wide limiter for ``(account, kind)``, created from settings.

    Writes use the cross-process :class:`SharedRateLimiter` (``~/.heliograph/ratelimit.json``).
    """
    key = (account, kind)
    if key not in _REGISTRY:
        s = settings or get_settings()
        if kind == "read":
            _REGISTRY[key] = RateLimiter(s.read_min_interval, s.read_jitter, name=f"{account}.read")
        else:
            # interval/jitter are read from settings at each acquire (shared state file)
            _REGISTRY[key] = SharedRateLimiter(SharedWriteLimiter(name=f"{account}.write"),
                                               name=f"{account}.write")
    return _REGISTRY[key]
