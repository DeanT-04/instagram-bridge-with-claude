"""MCP server browser lifetime: close on shutdown, idle auto-shutdown, transparent relaunch."""

from __future__ import annotations

import asyncio

import pytest

from heliograph.config import get_settings
from heliograph.mcp import common
from heliograph.mcp.runtime import Runtime
from tests.mcp_server.conftest import FakeCdp, Harness


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _runtime(harness: Harness, clock: Clock) -> Runtime:
    rt = Runtime(cdp_factory=FakeCdp, service_factory=harness.runtime._service_factory,
                 clock=clock)
    return rt


def test_idle_default_is_15_minutes() -> None:
    assert get_settings().browser_idle_minutes == 15


async def test_idle_close_and_transparent_relaunch(harness: Harness) -> None:
    clock = Clock()
    rt = _runtime(harness, clock)
    rt.cdp_driver()
    await rt.service()
    first = rt._cdp
    assert isinstance(first, FakeCdp) and len(harness.service_builds) == 1

    clock.now += 14 * 60
    assert await rt.close_if_idle() is False  # not idle long enough
    clock.now += 2 * 60
    assert await rt.close_if_idle() is True
    assert first.shutdowns == 1 and rt._cdp is None and rt._service is None
    assert await rt.close_if_idle() is False  # nothing left to close

    await rt.service()  # next ig_* call rebuilds (and would relaunch) transparently
    assert len(harness.service_builds) == 2


async def test_idle_never_closes_during_a_tool_call(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Clock()
    rt = _runtime(harness, clock)
    rt.cdp_driver()
    clock.now += 60 * 60
    monkeypatch.setattr("heliograph.mcp.runtime.tools_in_flight", lambda: 1)
    assert await rt.close_if_idle() is False
    monkeypatch.setattr("heliograph.mcp.runtime.tools_in_flight", lambda: 0)
    assert await rt.close_if_idle() is False  # the running call reset the idle timer
    clock.now += 15 * 60
    assert await rt.close_if_idle() is True


async def test_idle_zero_disables(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HELIOGRAPH_BROWSER_IDLE_MINUTES", "0")
    get_settings.cache_clear()
    clock = Clock()
    rt = _runtime(harness, clock)
    rt.cdp_driver()
    clock.now += 10**6
    assert await rt.close_if_idle() is False
    rt.start_idle_watch()
    assert rt._idle_task is None


async def test_tool_wrapper_counts_calls_in_flight(harness: Harness) -> None:
    seen: list[int] = []
    original = harness.runtime.service

    async def spy() -> object:
        seen.append(common.tools_in_flight())
        return await original()

    harness.runtime.service = spy  # type: ignore[method-assign]
    await harness.server.call_tool("ig_whoami", {})
    assert seen == [1] and common.tools_in_flight() == 0


async def test_aclose_stops_watch_and_shuts_browser_down(harness: Harness) -> None:
    rt = harness.runtime
    rt.start_idle_watch()
    task = rt._idle_task
    assert task is not None
    drv = rt.cdp_driver()
    await rt.aclose()
    await asyncio.sleep(0)
    assert task.cancelled() or task.done()
    assert isinstance(drv, FakeCdp) and drv.shutdowns == 1
