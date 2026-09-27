"""Fast status: cached Get-AppxPackage, last-known login state, no attach for status."""

from __future__ import annotations

import subprocess
import sys
from typing import Any

import pytest

from heliograph.commands.doctor import doctor_rows
from heliograph.detect import detect_environment, last_login_state
from heliograph.detect import instagram_app as ig
from heliograph.detect.models import NOT_INSTALLED
from heliograph.drivers.cdp.devtools import CdpEndpoint
from heliograph.drivers.cdp.launcher import BrowserLauncher
from tests.mcp_server.conftest import Harness, json_of
from tests.mcp_server.test_status import report

PKG = '{"Name":"Facebook.InstagramBeta","Version":"1.0","PackageFamilyName":"F_x"}'


def _proc(stdout: str = "", code: int = 0) -> Any:
    return subprocess.CompletedProcess(args=[], returncode=code, stdout=stdout, stderr="boom")


@pytest.fixture
def windows(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ig, "find_instagram_window", lambda: (True, "Instagram", None))
    return calls


def test_appx_result_is_cached(windows: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ig, "_run_powershell",
                        lambda c, timeout=30: windows.append(c) or _proc(PKG))
    assert ig.detect_instagram_app().installed
    assert ig.detect_instagram_app().installed
    assert len(windows) == 1
    ig.clear_package_cache()
    ig.detect_instagram_app()
    assert len(windows) == 2


def test_appx_failure_not_cached_and_not_installed_expires(
    windows: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ig, "_run_powershell",
                        lambda c, timeout=30: windows.append(c) or _proc(code=1))
    assert "failed" in (ig.detect_instagram_app().reason or "")
    ig.detect_instagram_app()
    assert len(windows) == 2  # failures are re-checked
    monkeypatch.setattr(ig, "_run_powershell",
                        lambda c, timeout=30: windows.append(c) or _proc(""))
    assert ig.detect_instagram_app().reason == NOT_INSTALLED
    ig.detect_instagram_app()
    assert len(windows) == 3  # "not installed" cached...
    monkeypatch.setattr(ig, "_NOT_INSTALLED_TTL", -1.0)
    ig.detect_instagram_app()
    assert len(windows) == 4  # ...but only for a short while


def test_record_login_and_needs_login(monkeypatch: pytest.MonkeyPatch) -> None:
    import heliograph.detect as detect

    launcher = BrowserLauncher()
    assert launcher.last_login() is None and last_login_state() is None
    launcher.record_login(False)
    assert launcher.last_login() is False and last_login_state() is False
    monkeypatch.setattr(detect, "find_browsers", lambda: [])
    monkeypatch.setattr(detect, "detect_instagram_app",
                        lambda check_window=True: ig.InstagramAppInfo(supported=False))
    env = detect_environment()
    assert env.logged_in is False and env.needs_login
    launcher.record_login(True)
    assert detect_environment().logged_in is True


def test_needs_login_rules() -> None:
    r = report()
    assert r.needs_login  # profile never created
    r.browser_profile_initialized = True
    assert not r.needs_login  # created, never checked: assume fine
    r.logged_in = False
    assert r.needs_login


def test_doctor_hints_use_uv_run_and_skip_store_when_check_failed() -> None:
    r = report()
    hints = {check: hint for _, check, _, hint in doctor_rows(r)}
    assert hints["Instagram Store app"].startswith("uv run heliograph setup")
    assert hints["CDP browser profile"].startswith("uv run heliograph login")
    r.instagram_app.reason = "Get-AppxPackage failed: access denied"
    hints = {check: hint for _, check, _, hint in doctor_rows(r)}
    assert "Store" not in hints["Instagram Store app"]


class RunningLauncher:
    def __init__(self, last: bool | None) -> None:
        self.last = last

    def find_running(self) -> CdpEndpoint:
        return CdpEndpoint(port=9, ws_url="ws://127.0.0.1:9/devtools/browser/x", browser="E")

    def last_login(self) -> bool | None:
        return self.last


@pytest.mark.parametrize("last", [True, False, None])
async def test_status_does_not_attach_to_a_running_browser(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, last: bool | None
) -> None:
    monkeypatch.setattr("heliograph.detect.detect_environment",
                        lambda check_window=True: report())
    driver = harness.runtime.cdp_driver()
    driver.launcher = RunningLauncher(last)

    async def no_connect() -> None:
        raise AssertionError("status must not attach to the browser")

    driver.connect = no_connect
    out = json_of(await harness.server.call_tool("heliograph_status", {}))
    assert out["login"].get("logged_in") == last
    assert "browser running" in out["login"]["detail"]


async def test_status_reads_live_login_when_connected(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("heliograph.detect.detect_environment",
                        lambda check_window=True: report())
    driver = harness.runtime.cdp_driver()

    async def connected() -> str:
        return "https://www.instagram.com/"

    async def logged_in() -> bool:
        return True

    driver.current_url = connected
    driver.is_logged_in = logged_in
    out = json_of(await harness.server.call_tool("heliograph_status", {}))
    assert out["login"] == {"logged_in": True, "detail": "logged in",
                            "profile_initialized": False}
