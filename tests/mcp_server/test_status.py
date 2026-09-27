"""heliograph_status / heliograph_setup_check with a mocked environment."""

from __future__ import annotations

import pytest

from heliograph.detect.models import (
    NOT_INSTALLED,
    BrowserInfo,
    EnvironmentReport,
    InstagramAppInfo,
    PackageInfo,
    ToolInfo,
)
from tests.mcp_server.conftest import Harness, json_of


def report(*, app_installed: bool = False, ffmpeg: bool = False) -> EnvironmentReport:
    return EnvironmentReport(
        os="windows", os_version="Windows-11", python_version="3.12.4",
        instagram_app=InstagramAppInfo(supported=True, installed=app_installed,
                                       reason=None if app_installed else NOT_INSTALLED),
        browsers=[BrowserInfo(channel="msedge", path="C:/edge.exe", source="registry")],
        ffmpeg=ToolInfo(name="ffmpeg", found=ffmpeg), ffprobe=ToolInfo(name="ffprobe",
                                                                       found=ffmpeg),
        playwright=PackageInfo(name="playwright", importable=True),
        uiautomation=PackageInfo(name="uiautomation", importable=True),
        browser_profile_dir="C:/p", browser_profile_initialized=False,
    )


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    opened: list[str] = []
    monkeypatch.setattr("heliograph.detect.detect_environment",
                        lambda check_window=True: report())
    monkeypatch.setattr("heliograph.detect.install_instagram_app",
                        lambda: opened.append("store") or "Opened the Store")
    return opened


async def test_status(harness: Harness, env: list[str]) -> None:
    out = json_of(await harness.server.call_tool("heliograph_status", {}))
    assert out["ready"] is True and out["drivers"]["cdp"]["available"] is True
    assert out["drivers"]["uia"]["available"] is False
    assert "logged_in" not in out["login"] and "not running" in out["login"]["detail"]


async def test_setup_check_lists_steps_and_only_opens_store_on_request(
    harness: Harness, env: list[str]
) -> None:
    out = json_of(await harness.server.call_tool("heliograph_setup_check", {}))
    items = [s["item"] for s in out["steps"]]
    assert items == ["ffmpeg", "login", "instagram_app"]
    assert "apps.microsoft.com" in out["steps"][-1]["action"] and env == []
    out = json_of(await harness.server.call_tool("heliograph_setup_check", {"open_store": True}))
    assert out["store"] == "Opened the Store" and env == ["store"]
