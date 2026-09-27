"""Cross-process write rate limit shared by every Heliograph process (CDP and UIA writes).

The MCP server, the CLI and tests can run as separate processes at once, so an in-memory
limiter would let each of them write at full speed. The next allowed write time lives in
``~/.heliograph/ratelimit.json`` instead, updated under a small portable lock file
(``os.open(O_CREAT | O_EXCL)``; a lock older than ``stale_after`` seconds is assumed to be
left over by a crashed process and removed). No extra dependencies.

A write *reserves* a slot: under the lock it reads ``next_allowed``, takes
``max(now, next_allowed)`` and stores ``slot + write_min_interval + U(0, write_jitter)``.
The lock is never held while sleeping, so concurrent writers queue up in slot order.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from heliograph.config import ensure_private_dir, get_settings
from heliograph.errors import RateLimitedError
from heliograph.eye import event

__all__ = ["FileLock", "SharedWriteLimiter", "shared_write_limiter"]


class FileLock:
    """Exclusive lock file (``<path>``) usable across processes.

    Args:
        path: The lock file to create.
        timeout: Seconds to keep trying before :class:`TimeoutError`.
        stale_after: A lock file older than this is removed (crashed holder).
    """

    def __init__(
        self, path: Path, *, timeout: float = 10.0, stale_after: float = 30.0, poll: float = 0.02
    ) -> None:
        self.path = path
        self.timeout = timeout
        self.stale_after = stale_after
        self.poll = poll

    def _break_if_stale(self) -> None:
        try:
            age = time.time() - self.path.stat().st_mtime
        except FileNotFoundError:
            return
        if age > self.stale_after:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass

    @contextmanager
    def hold(self) -> Iterator[None]:
        """Acquire the lock for the ``with`` block."""
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                break
            except FileExistsError:
                self._break_if_stale()
            except PermissionError:  # Windows: file is being deleted by another process
                pass
            if time.monotonic() >= deadline:
                raise TimeoutError(f"could not acquire {self.path} within {self.timeout}s")
            time.sleep(self.poll)
        try:
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            yield
        finally:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass


class SharedWriteLimiter:
    """``min_interval + U(0, jitter)`` seconds between writes, across processes.

    Args:
        path: The state file; default ``<settings.home>/ratelimit.json`` (resolved per call,
            so a changed ``HELIOGRAPH_HOME`` takes effect).
        min_interval/jitter: Default ``settings.write_min_interval`` / ``write_jitter``.
        clock/rand/sleep: Injectable for tests (``clock`` must be wall-clock time).
        lock_timeout: Seconds to wait for the lock file before :class:`RateLimitedError`.
    """

    def __init__(
        self,
        path: Path | None = None,
        *,
        min_interval: float | None = None,
        jitter: float | None = None,
        clock: Callable[[], float] = time.time,
        rand: Callable[[], float] = random.random,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        name: str = "write",
        lock_timeout: float = 10.0,
    ) -> None:
        self._path = path
        self._min_interval = min_interval
        self._jitter = jitter
        self._clock = clock
        self._rand = rand
        self._sleep = sleep
        self.name = name
        self.lock_timeout = lock_timeout

    @property
    def path(self) -> Path:
        """The shared state file."""
        return self._path or get_settings().home / "ratelimit.json"

    def _gap(self) -> float:
        s = get_settings()
        interval = self._min_interval if self._min_interval is not None else s.write_min_interval
        jitter = self._jitter if self._jitter is not None else s.write_jitter
        return interval + jitter * self._rand()

    def _read(self) -> float:
        try:
            data = json.loads(self.path.read_text("utf-8"))
            return float(data.get("next_allowed", 0.0))
        except (OSError, ValueError, TypeError, AttributeError):
            return 0.0

    def _write(self, next_allowed: float, last: float) -> None:
        tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"next_allowed": next_allowed, "last": last}), "utf-8")
        os.replace(tmp, self.path)

    def _transact(self, *, block: bool) -> float:
        ensure_private_dir(self.path.parent)
        lock = FileLock(self.path.with_name(self.path.name + ".lock"), timeout=self.lock_timeout)
        try:
            with lock.hold():
                now = self._clock()
                next_allowed = self._read()
                # a far-future value (clock change, corrupted file) must not block forever
                next_allowed = min(next_allowed, now + 24 * 3600)
                wait = max(0.0, next_allowed - now)
                if wait > 0 and not block:
                    return wait
                slot = now + wait
                self._write(slot + self._gap(), slot)
                return wait
        except TimeoutError as exc:
            raise RateLimitedError(
                "the shared write limiter is busy", retry_after=1.0, hint=str(exc)
            ) from exc

    def remaining(self) -> float:
        """Seconds until the next write is allowed (no reservation)."""
        return max(0.0, self._read() - self._clock())

    def try_acquire(self, op: str = "write") -> None:
        """Reserve a write slot now or raise :class:`RateLimitedError` (no waiting)."""
        wait = self._transact(block=False)
        if wait > 0:
            raise RateLimitedError(
                f"{op}: writes are rate limited (shared across Heliograph processes)",
                retry_after=wait,
            )

    async def acquire(self) -> float:
        """Reserve the next slot and wait for it; returns the seconds waited."""
        wait = await asyncio.to_thread(self._transact, block=True)
        if wait > 0:
            event("ratelimit.wait", limiter=self.name, seconds=round(wait, 3), shared=True)
            await self._sleep(wait)
        return wait


def shared_write_limiter() -> SharedWriteLimiter:
    """The settings-derived shared write limiter (state under ``settings.home``)."""
    return SharedWriteLimiter()
