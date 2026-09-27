"""Cross-process write rate limit shared by every Heliograph process (CDP and UIA writes).

The MCP server, the CLI and tests can run as separate processes at once, so an in-memory
limiter would let each of them write at full speed. The next allowed write time lives in
``~/.heliograph/ratelimit.json`` instead, updated under a small portable lock file
(``os.open(O_CREAT | O_EXCL)`` + an ownership token re-read before use; a lock older than
``stale_after`` seconds is assumed to be left over by a crashed process and broken by an
atomic rename, so two processes can never both hold it). No extra dependencies.

A write *reserves* a slot: under the lock it reads ``next_allowed``, takes
``max(now, next_allowed)`` and stores ``slot + write_min_interval + U(0, write_jitter)``.
The lock is never held while sleeping, so concurrent writers queue up in slot order.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import secrets
import sys
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from heliograph.config import ensure_private_dir, get_settings
from heliograph.errors import RateLimitedError
from heliograph.eye import event

__all__ = ["FileLock", "SharedWriteLimiter", "shared_write_limiter"]


def _unlink(path: Path, attempts: int = 200) -> None:
    """Remove ``path``; on Windows retry while another process briefly has it open."""
    for _ in range(attempts):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            time.sleep(0.005)
    path.unlink(missing_ok=True)  # last try: let the error surface


class FileLock:
    """Exclusive lock file (``<path>``) usable across processes.

    The holder writes a unique token (``pid:nonce``) into the lock and re-reads it before
    entering the ``with`` block, so it only proceeds while the lock file is provably its
    own. A stale lock is broken by *renaming* it to a unique name (atomic: only one
    waiter can take a given file) and checking the renamed file still carries the stale
    token; if a live lock was grabbed by mistake it is put back without overwriting. On
    release only a lock holding our own token is removed.

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

    def _read(self, path: Path | None = None) -> str | None:
        try:
            return (path or self.path).read_text("utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    def _break_if_stale(self) -> None:
        try:
            age = time.time() - self.path.stat().st_mtime
        except FileNotFoundError:
            return
        if age <= self.stale_after:
            return
        stale_token = self._read()
        tomb = self.path.with_name(f"{self.path.name}.{secrets.token_hex(6)}.stale")
        try:
            os.rename(self.path, tomb)  # atomic: of several breakers only one gets the file
        except OSError:  # gone already, or open/being replaced by another process
            return
        if self._read(tomb) == stale_token:
            _unlink(tomb)
            return
        # The lock was replaced between our check and the rename: that is a live lock.
        # Put it back without overwriting a lock someone created in the meantime.
        try:
            if sys.platform == "win32":
                os.rename(tomb, self.path)  # fails if path exists
            else:
                os.link(tomb, self.path)  # fails if path exists
                _unlink(tomb)
        except OSError:
            _unlink(tomb)  # its owner sees the token change and retries

    def _try_create(self, token: str) -> bool:
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            if self._read() == token:  # our own lock, restored by a breaker
                return True
            self._break_if_stale()
            return False
        except PermissionError:  # Windows: file is being deleted by another process
            return False
        try:
            os.write(fd, token.encode())
            os.fsync(fd)
        finally:
            os.close(fd)
        # Re-verify: a waiter that judged an older lock stale may have removed ours.
        return self._read() == token

    @contextmanager
    def hold(self) -> Iterator[None]:
        """Acquire the lock for the ``with`` block."""
        token = f"{os.getpid()}:{secrets.token_hex(8)}"
        deadline = time.monotonic() + self.timeout
        while not self._try_create(token):
            if time.monotonic() >= deadline:
                raise TimeoutError(f"could not acquire {self.path} within {self.timeout}s")
            time.sleep(self.poll)
        try:
            yield
        finally:
            if self._read() == token:  # never delete a lock that is not ours
                _unlink(self.path)


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
