from __future__ import annotations

import subprocess
import sys
from typing import Any

import pytest

from heliograph.detect import (
    EnvironmentReport,
    detect_environment,
    detect_instagram_app,
    detect_package,
    detect_tool,
    find_browsers,
    install_instagram_app,
)
from heliograph.detect import instagram_app as ig
from heliograph.detect import tools as tools_mod

PKG_JSON = (
    '{"Name":"Facebook.InstagramBeta","Version":"42.0.23.0",'
    '"PackageFamilyName":"Facebook.InstagramBeta_8xx8rvfyw5nnt",'
    '"InstallLocation":"C:\\\\Program Files\\\\WindowsApps\\\\Facebook.InstagramBeta_42"}'
)


def _completed(stdout: str = "", returncode: int = 0, stderr: str = "") -> Any:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout,
                                       stderr=stderr)


@pytest.fixture
def windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ig, "find_instagram_window", lambda: (True, "Instagram", None))


def test_app_installed(windows: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ig, "_run_powershell", lambda cmd, timeout=30: _completed(PKG_JSON))
    info = detect_instagram_app()
    assert info.installed and info.version == "42.0.23.0"
    assert info.aumid == "Facebook.InstagramBeta_8xx8rvfyw5nnt!App"
    assert info.window_open is True and info.window_title == "Instagram"


def test_app_multiple_versions_list(windows: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ig, "_run_powershell", lambda c, timeout=30: _completed(f"[{PKG_JSON}]"))
    assert detect_instagram_app(check_window=False).installed


def test_app_not_installed(windows: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ig, "_run_powershell", lambda c, timeout=30: _completed(""))
    info = detect_instagram_app()
    assert info.supported and not info.installed and info.reason == "not installed"
    assert info.window_open is None


@pytest.mark.parametrize(
    "effect",
    [
        FileNotFoundError("powershell"),
        subprocess.TimeoutExpired("powershell", 30),
        lambda: _completed("{not json"),
        lambda: _completed("", returncode=1, stderr="Access denied"),
    ],
)
def test_app_failures_never_raise(windows: None, monkeypatch: pytest.MonkeyPatch,
                                  effect: Any) -> None:
    def fake(cmd: str, timeout: float = 30) -> Any:
        if isinstance(effect, BaseException):
            raise effect
        return effect()

    monkeypatch.setattr(ig, "_run_powershell", fake)
    info = detect_instagram_app()
    assert not info.installed and info.reason


def test_non_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    info = detect_instagram_app()
    assert not info.supported and not info.installed
    assert "web" in install_instagram_app().lower()


def test_install_opens_store(windows: None, monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(ig.os, "startfile", opened.append, raising=False)
    msg = install_instagram_app()
    assert opened == ["ms-windows-store://pdp/?ProductId=9NBLGGH5L9XT"] and "Store" in msg


def test_find_browsers_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    from heliograph.detect import browsers

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(browsers, "registry_app_path",
                        lambda exe: r"C:\Edge\msedge.exe" if exe == "msedge.exe" else None)
    monkeypatch.setenv("PROGRAMFILES", r"C:\PF")
    chrome = r"C:\PF\Google\Chrome\Application\chrome.exe"
    found = find_browsers(exists=lambda p: p in (r"C:\Edge\msedge.exe", chrome))
    assert [(b.channel, b.source) for b in found] == [("msedge", "registry"),
                                                     ("chrome", "common-location")]


def test_find_browsers_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    from heliograph.detect import browsers

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(browsers.shutil, "which",
                        lambda n: "/usr/bin/chromium" if n == "chromium" else None)
    found = find_browsers(exists=lambda p: p == "/usr/bin/chromium")
    assert [b.channel for b in found] == ["chromium"]


def test_find_browsers_none() -> None:
    assert find_browsers(exists=lambda p: False) == []


def test_detect_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools_mod.shutil, "which", lambda n: None)
    assert not detect_tool("ffmpeg").found
    monkeypatch.setattr(tools_mod.shutil, "which", lambda n: "/bin/ffmpeg")
    monkeypatch.setattr(tools_mod.subprocess, "run", lambda *a, **k: _completed(
        "ffmpeg version 9.0 Copyright (c) 2000 the FFmpeg developers\nmore"))
    t = detect_tool("ffmpeg")
    assert t.found and t.version == "ffmpeg version 9.0"

    def boom(*a: Any, **k: Any) -> Any:
        raise OSError("exec format error")

    monkeypatch.setattr(tools_mod.subprocess, "run", boom)
    t = detect_tool("ffmpeg")
    assert t.found and t.version is None and "exec format" in (t.reason or "")


def test_detect_package() -> None:
    assert detect_package("json").importable
    missing = detect_package("definitely_not_a_module_xyz")
    assert not missing.importable and "ModuleNotFoundError" in (missing.reason or "")


def test_detect_environment_composes(monkeypatch: pytest.MonkeyPatch) -> None:
    import heliograph.detect as detect

    monkeypatch.setattr(detect, "find_browsers", lambda: [])
    monkeypatch.setattr(detect, "detect_instagram_app",
                        lambda check_window=True: ig.InstagramAppInfo(supported=False))
    report = detect_environment()
    assert isinstance(report, EnvironmentReport)
    assert "browser" in report.critical_missing and not report.ok
    assert report.browser_profile_initialized is False


def test_powershell_is_invoked_by_absolute_path(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    monkeypatch.setenv("SYSTEMROOT", r"C:\Windows")
    exe = ig._powershell_exe()
    assert exe.lower().endswith(os.path.join("system32", "windowspowershell", "v1.0",
                                             "powershell.exe"))
    monkeypatch.setenv("SYSTEMROOT", "relative")  # tampered/relative value is not trusted
    assert ig._powershell_exe().lower().startswith("c:")
    calls: list[list[str]] = []
    monkeypatch.setattr(ig.subprocess, "run",
                        lambda args, **k: calls.append(args) or _completed(""))
    ig._run_powershell("Get-Date")
    assert os.path.isabs(calls[0][0]) or calls[0][0].lower().startswith("c:")


def test_detect_tool_ignores_current_directory_on_windows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    bin_dir = tmp_path / "bin"
    monkeypatch.setenv("PATH", ";".join([".", "relative", str(bin_dir)]))
    looked: list[str] = []

    def fake_which(cmd: str) -> str | None:
        looked.append(cmd)
        return cmd if cmd.startswith(str(bin_dir)) else None

    monkeypatch.setattr(tools_mod.shutil, "which", fake_which)
    assert tools_mod._which("ffmpeg") == str(bin_dir / "ffmpeg")
    assert looked == [str(bin_dir / "ffmpeg")]  # "." and relative entries skipped
