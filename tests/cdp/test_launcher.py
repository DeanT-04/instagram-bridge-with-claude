"""Launcher, DevTools discovery and rate limiter (no real browser)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from heliograph.config import get_settings
from heliograph.drivers.cdp import devtools, launcher
from heliograph.drivers.cdp.devtools import BrowserProcess, CdpEndpoint
from heliograph.drivers.cdp.launcher import BrowserLauncher
from heliograph.drivers.cdp.ratelimit import RateLimiter
from heliograph.errors import BrowserNotFoundError


def _ep(port: int) -> CdpEndpoint:
    return CdpEndpoint(port=port, ws_url=f"ws://127.0.0.1:{port}/devtools/browser/x", browser="E")


def test_read_devtools_active_port(tmp_path: Path) -> None:
    assert devtools.read_devtools_active_port(tmp_path) is None
    (tmp_path / "DevToolsActivePort").write_text("9555\n/devtools/browser/abc\n")
    assert devtools.read_devtools_active_port(tmp_path) == 9555
    (tmp_path / "DevToolsActivePort").write_text("junk")
    assert devtools.read_devtools_active_port(tmp_path) is None


def test_parse_cmdline_port() -> None:
    prof = Path(r"C:\Users\me\.heliograph\browser-profile")
    line = (r'"C:\Edge\msedge.exe" --user-data-dir="C:\Users\me\.heliograph\browser-profile" '
            "--remote-debugging-port=9334 --app=https://www.instagram.com/")
    assert devtools.parse_cmdline_port(line, prof) == 9334
    assert devtools.parse_cmdline_port(line + " --type=renderer", prof) is None
    assert devtools.parse_cmdline_port(line, Path(r"C:\other")) is None
    assert devtools.parse_cmdline_port("msedge --remote-debugging-port=9333", prof) is None


def test_free_port_is_usable() -> None:
    assert 0 < devtools.free_port() < 65536


def test_profile_dirs(isolated_home: Path) -> None:
    assert BrowserLauncher().profile_dir == get_settings().profile_path
    assert BrowserLauncher(account="alt").profile_dir == isolated_home / "profiles" / "alt"
    with pytest.raises(ValueError):
        BrowserLauncher(account="../evil")


def test_find_running_via_process_scan_records_state(
    monkeypatch: pytest.MonkeyPatch, isolated_home: Path
) -> None:
    monkeypatch.setattr(devtools, "find_browser_process", lambda p: BrowserProcess(42, 9400))
    monkeypatch.setattr(devtools, "probe_endpoint", lambda port, timeout=2.0: _ep(port))
    ep = BrowserLauncher().find_running()
    assert ep is not None and ep.port == 9400
    state = json.loads((isolated_home / "state.json").read_text())
    assert state["cdp"]["default"]["port"] == 9400 and state["cdp"]["default"]["pid"] == 42


def test_find_running_prefers_devtools_active_port(
    monkeypatch: pytest.MonkeyPatch, isolated_home: Path
) -> None:
    prof = BrowserLauncher().profile_dir
    prof.mkdir(parents=True)
    (prof / "DevToolsActivePort").write_text("9500\n/x\n")
    monkeypatch.setattr(devtools, "find_browser_process", lambda p: None)
    monkeypatch.setattr(devtools, "probe_endpoint", lambda port, timeout=2.0: _ep(port))
    ep = BrowserLauncher().find_running()
    assert ep is not None and ep.port == 9500


def test_stale_state_port_is_ignored(monkeypatch: pytest.MonkeyPatch, isolated_home: Path) -> None:
    devtools.write_state(get_settings().state_file, cdp={"default": {"port": 9600}})
    monkeypatch.setattr(devtools, "find_browser_process", lambda p: None)
    monkeypatch.setattr(devtools, "probe_endpoint", lambda port, timeout=2.0: None)
    assert BrowserLauncher().find_running() is None


def test_launch_uses_localhost_flags(monkeypatch: pytest.MonkeyPatch, isolated_home: Path) -> None:
    calls: list[list[str]] = []

    class Proc:
        pid = 7

    def fake_popen(args: list[str], **kw: Any) -> Proc:
        calls.append(args)
        return Proc()

    monkeypatch.setattr(launcher, "find_browser_executable", lambda ch: ("msedge", "edge.exe"))
    monkeypatch.setattr(devtools, "free_port", lambda: 9777)
    monkeypatch.setattr(devtools, "probe_endpoint", lambda port, timeout=2.0: _ep(port))
    ep = BrowserLauncher(popen=fake_popen).launch()
    assert ep.port == 9777
    args = calls[0]
    assert "--remote-debugging-port=9777" in args
    assert "--remote-debugging-address=127.0.0.1" in args
    assert "--app=https://www.instagram.com/" in args
    assert any(a.startswith("--user-data-dir=") for a in args)
    assert BrowserLauncher().profile_dir.is_dir()


def test_find_browser_executable_order(monkeypatch: pytest.MonkeyPatch) -> None:
    from heliograph.detect.models import BrowserInfo

    found = [BrowserInfo(channel="chrome", path="c.exe", source="x"),
             BrowserInfo(channel="msedge", path="e.exe", source="x")]
    monkeypatch.setattr(launcher, "find_browsers", lambda: found)
    assert launcher.find_browser_executable("auto") == ("msedge", "e.exe")
    assert launcher.find_browser_executable("chrome") == ("chrome", "c.exe")
    monkeypatch.setattr(launcher, "find_browsers", lambda: [])
    monkeypatch.setattr(launcher, "_playwright_chromium", lambda: None)
    with pytest.raises(BrowserNotFoundError):
        launcher.find_browser_executable("auto")


def test_stop_signals_process(monkeypatch: pytest.MonkeyPatch, isolated_home: Path) -> None:
    ran: list[list[str]] = []
    monkeypatch.setattr(devtools, "find_browser_process", lambda p: BrowserProcess(99, 9400))
    monkeypatch.setattr(launcher.subprocess, "run", lambda args, **kw: ran.append(args))
    monkeypatch.setattr(launcher.os, "kill", lambda pid, sig: ran.append([str(pid)]))
    assert BrowserLauncher().stop() is True
    assert "99" in ran[0]


async def test_rate_limiter_spacing() -> None:
    now = [100.0]
    slept: list[float] = []

    async def sleep(s: float) -> None:
        slept.append(s)
        now[0] += s

    lim = RateLimiter(2.0, 1.0, clock=lambda: now[0], sleep=sleep, rand=lambda: 0.5)
    assert await lim.acquire() == 0.0
    now[0] += 0.5
    assert await lim.acquire() == pytest.approx(2.0)  # 2 + 0.5 jitter - 0.5 elapsed
    now[0] += 10
    assert await lim.acquire() == 0.0
    assert slept == [pytest.approx(2.0)]
