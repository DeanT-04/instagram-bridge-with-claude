"""The cross-process write limiter (ratelimit.json + lock file) shared by CDP and UIA."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from heliograph.config import get_settings
from heliograph.drivers.cdp.ratelimit import SharedRateLimiter, limiter_for
from heliograph.drivers.uia.writes import WriteActions
from heliograph.errors import InstagramApiError, RateLimitedError
from heliograph.writelimit import FileLock, SharedWriteLimiter


class _Clock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_two_limiters_on_one_file_share_the_budget(tmp_path: Path) -> None:
    clock = _Clock()
    path = tmp_path / "ratelimit.json"
    a = SharedWriteLimiter(path, min_interval=20, jitter=10, clock=clock, rand=lambda: 0.5)
    b = SharedWriteLimiter(path, min_interval=20, jitter=10, clock=clock, rand=lambda: 0.5)
    a.try_acquire()
    with pytest.raises(RateLimitedError) as err:
        b.try_acquire("like")
    assert err.value.retry_after == pytest.approx(25.0)  # 20 + 10 * 0.5 jitter
    assert b.remaining() == pytest.approx(25.0)
    clock.now += 25.0
    b.try_acquire()
    assert json.loads(path.read_text("utf-8"))["last"] == clock.now


async def test_acquire_queues_behind_reserved_slots(tmp_path: Path) -> None:
    clock, slept = _Clock(), []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    lim = SharedWriteLimiter(
        tmp_path / "r.json", min_interval=5, jitter=0, clock=clock, sleep=sleep
    )
    assert await lim.acquire() == 0.0
    assert await lim.acquire() == pytest.approx(5.0)
    assert await lim.acquire() == pytest.approx(10.0)  # slots are reserved, not re-raced
    assert slept == [pytest.approx(5.0), pytest.approx(10.0)]


def test_jitter_and_interval_come_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HELIOGRAPH_WRITE_MIN_INTERVAL", "30")
    monkeypatch.setenv("HELIOGRAPH_WRITE_JITTER", "4")
    get_settings.cache_clear()
    clock = _Clock()
    lim = SharedWriteLimiter(clock=clock, rand=lambda: 1.0)
    lim.try_acquire()
    assert lim.path == get_settings().home / "ratelimit.json"
    assert lim.remaining() == pytest.approx(34.0)


def test_corrupt_or_far_future_state_does_not_block_forever(tmp_path: Path) -> None:
    path = tmp_path / "r.json"
    path.write_text("{not json", "utf-8")
    lim = SharedWriteLimiter(path, min_interval=1, jitter=0, clock=_Clock())
    lim.try_acquire()
    path.write_text(json.dumps({"next_allowed": 1e12}), "utf-8")
    assert lim._transact(block=False) <= 24 * 3600


def test_stale_lock_is_broken(tmp_path: Path) -> None:
    lock_path = tmp_path / "x.lock"
    lock_path.write_text("12345")
    old = time.time() - 120
    os.utime(lock_path, (old, old))
    with FileLock(lock_path, timeout=1, stale_after=30).hold():
        assert lock_path.exists()
    assert not lock_path.exists()


def test_held_lock_times_out(tmp_path: Path) -> None:
    lock_path = tmp_path / "x.lock"
    lock_path.write_text("busy")
    with pytest.raises(TimeoutError), FileLock(lock_path, timeout=0.2, stale_after=60).hold():
        pass
    lim = SharedWriteLimiter(tmp_path / "r.json", min_interval=1, jitter=0, lock_timeout=0.2)
    (tmp_path / "r.json.lock").write_text("busy")
    with pytest.raises(RateLimitedError):  # never write without the lock
        lim.try_acquire()


def test_cdp_and_uia_writes_use_the_same_state_file() -> None:
    cdp = limiter_for("write", "someone")
    assert isinstance(cdp, SharedRateLimiter)
    uia = WriteActions.write_limiter
    assert cdp.shared.path == uia.path == get_settings().home / "ratelimit.json"
    uia.try_acquire()
    with pytest.raises(RateLimitedError):
        cdp.shared.try_acquire()


def test_limit_holds_across_processes(tmp_path: Path) -> None:
    """A write reserved in another Python process blocks this one."""
    path = tmp_path / "ratelimit.json"
    code = (
        "import sys; from pathlib import Path; from heliograph.writelimit import "
        "SharedWriteLimiter as L; L(Path(sys.argv[1]), min_interval=60, jitter=0)"
        ".try_acquire()"
    )
    subprocess.run([sys.executable, "-c", code, str(path)], check=True, timeout=60)
    lim = SharedWriteLimiter(path, min_interval=60, jitter=0)
    with pytest.raises(RateLimitedError) as err:
        lim.try_acquire()
    assert err.value.retry_after is not None and 55 < err.value.retry_after <= 60


def test_instagram_api_error_moved_with_reexport() -> None:
    from heliograph.drivers.cdp import webapi

    assert webapi.InstagramApiError is InstagramApiError
    err = InstagramApiError("x", status=404, path="/api/v1/x/")
    assert (err.status, err.path) == (404, "/api/v1/x/")


def test_lock_holds_an_ownership_token_and_release_spares_foreign_locks(tmp_path: Path) -> None:
    lock_path = tmp_path / "x.lock"
    with FileLock(lock_path, timeout=1).hold():
        pid, _, nonce = lock_path.read_text().partition(":")
        assert pid == str(os.getpid()) and len(nonce) == 16
        lock_path.write_text("someone-else")  # e.g. ours was broken as stale meanwhile
    assert lock_path.read_text() == "someone-else"  # not deleted: it is not ours


def test_breaking_a_lock_that_was_just_replaced_puts_it_back(tmp_path: Path) -> None:
    """B judged an old lock stale, but A replaced it before B's rename: A keeps it."""
    lock_path = tmp_path / "x.lock"
    with FileLock(lock_path, timeout=1).hold():
        live = lock_path.read_text()
        old = time.time() - 120
        os.utime(lock_path, (old, old))
        b = FileLock(lock_path, timeout=0.2, stale_after=30)
        real_read = b._read
        seen = iter(["999:stale-token"])  # what B read before A's lock replaced it
        b._read = lambda path=None: next(seen) if path is None else real_read(path)  # type: ignore[method-assign]
        b._break_if_stale()
        assert lock_path.read_text() == live
        assert not list(tmp_path.glob("*.stale"))
        os.utime(lock_path, None)  # fresh again: B must wait, not steal it
        with pytest.raises(TimeoutError), FileLock(lock_path, timeout=0.2).hold():
            pass
    assert not lock_path.exists()


def test_concurrent_breakers_of_a_stale_lock_never_both_hold_it(tmp_path: Path) -> None:
    import threading

    lock_path = tmp_path / "x.lock"
    inside = 0
    peak = 0
    guard = threading.Lock()

    def worker(barrier: threading.Barrier) -> None:
        nonlocal inside, peak
        barrier.wait()
        with FileLock(lock_path, timeout=5, stale_after=30, poll=0.001).hold():
            with guard:
                inside += 1
                peak = max(peak, inside)
            time.sleep(0.002)
            with guard:
                inside -= 1

    for _ in range(15):
        lock_path.write_text("1:crashed")
        old = time.time() - 120
        os.utime(lock_path, (old, old))
        barrier = threading.Barrier(6)
        threads = [threading.Thread(target=worker, args=(barrier,)) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not lock_path.exists()
    assert peak == 1
    assert not list(tmp_path.glob("*.stale"))
