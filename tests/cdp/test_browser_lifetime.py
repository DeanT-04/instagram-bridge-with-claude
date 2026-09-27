"""Browser lifetime: close only what we launched, never widen DevTools access (no browser)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from heliograph.drivers.cdp import devtools, launcher
from heliograph.drivers.cdp.devtools import BrowserProcess, CdpEndpoint
from heliograph.drivers.cdp.launcher import FORBIDDEN_ARGS, BrowserLauncher
from heliograph.drivers.cdp.session import CdpDriver


def _ep(port: int) -> CdpEndpoint:
    return CdpEndpoint(port=port, ws_url=f"ws://127.0.0.1:{port}/devtools/browser/x", browser="E")


class Proc:
    pid = 7


@pytest.fixture
def fake_launch(monkeypatch: pytest.MonkeyPatch, isolated_home: Path) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_popen(args: list[str], **kw: Any) -> Proc:
        calls.append(args)
        return Proc()

    monkeypatch.setattr(launcher, "find_browser_executable", lambda ch: ("msedge", "edge.exe"))
    monkeypatch.setattr(devtools, "free_port", lambda: 9777)
    monkeypatch.setattr(devtools, "probe_endpoint", lambda port, timeout=2.0: _ep(port))
    monkeypatch.setattr(launcher.subprocess, "Popen", fake_popen)
    return calls


@pytest.fixture
def signalled(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    pids: list[int] = []
    monkeypatch.setattr(launcher.subprocess, "run",
                        lambda args, **kw: pids.append(int(args[2])))
    monkeypatch.setattr(launcher.os, "kill", lambda pid, sig: pids.append(pid))
    return pids


def test_launch_args_never_widen_devtools_access(fake_launch: list[list[str]]) -> None:
    BrowserLauncher(popen=launcher.subprocess.Popen).launch()
    args = fake_launch[0]
    assert "--remote-debugging-address=127.0.0.1" in args
    for arg in args[1:]:
        for bad in FORBIDDEN_ARGS:
            assert bad not in arg
    assert not any(a.startswith("--remote-allow-origins") for a in args)


def test_stop_if_launched_closes_our_browser(
    fake_launch: list[list[str]], signalled: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    lnch = BrowserLauncher(popen=launcher.subprocess.Popen)
    lnch.launch()
    assert lnch.launched_port == 9777
    monkeypatch.setattr(devtools, "find_browser_process", lambda p: BrowserProcess(55, 9777))
    assert lnch.stop_if_launched() is True
    assert signalled == [55] and lnch.launched_port is None
    assert lnch.stop_if_launched() is False  # only once


def test_reused_browser_is_left_running(
    signalled: list[int], monkeypatch: pytest.MonkeyPatch, isolated_home: Path
) -> None:
    monkeypatch.setattr(devtools, "find_browser_process", lambda p: BrowserProcess(42, 9400))
    monkeypatch.setattr(devtools, "probe_endpoint", lambda port, timeout=2.0: _ep(port))
    lnch = BrowserLauncher()
    assert lnch.ensure_running().port == 9400  # reused, not launched
    assert lnch.launched_port is None
    assert lnch.stop_if_launched() is False
    assert signalled == []


def test_browser_on_another_port_is_not_ours(
    fake_launch: list[list[str]], signalled: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    lnch = BrowserLauncher(popen=launcher.subprocess.Popen)
    lnch.launch()
    # Ours died and something else now runs on the profile: leave it alone.
    monkeypatch.setattr(devtools, "find_browser_process", lambda p: BrowserProcess(56, 9888))
    assert lnch.stop_if_launched() is False
    assert signalled == []


class FakeLauncher:
    def __init__(self) -> None:
        self.stops = 0

    def stop_if_launched(self) -> bool:
        self.stops += 1
        return True


async def test_driver_shutdown_disconnects_then_stops() -> None:
    fake = FakeLauncher()
    driver = CdpDriver(launcher=fake, launch=False)  # type: ignore[arg-type]
    assert await driver.shutdown() is True
    assert fake.stops == 1 and driver._pw is None
