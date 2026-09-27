"""Environment detection: OS, Store Instagram app, browsers, ffmpeg, Python packages.

Every check is wrapped in an eye span and never raises; problems surface as fields
(``found=False``, ``reason=...``) on :class:`EnvironmentReport`.
"""

from __future__ import annotations

import platform
import sys

from heliograph.config import get_settings
from heliograph.detect.browsers import find_browsers
from heliograph.detect.instagram_app import (
    detect_instagram_app,
    find_instagram_window,
    install_instagram_app,
)
from heliograph.detect.models import (
    BrowserInfo,
    EnvironmentReport,
    InstagramAppInfo,
    PackageInfo,
    ToolInfo,
)
from heliograph.detect.tools import detect_package, detect_tool
from heliograph.eye import span

__all__ = [
    "BrowserInfo",
    "EnvironmentReport",
    "InstagramAppInfo",
    "PackageInfo",
    "ToolInfo",
    "current_os",
    "detect_environment",
    "detect_instagram_app",
    "detect_package",
    "detect_tool",
    "find_browsers",
    "find_instagram_window",
    "install_instagram_app",
]


def current_os() -> str:
    """Return ``windows``, ``macos``, ``linux`` or ``other``."""
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    return "other"


def detect_environment(*, check_window: bool = True) -> EnvironmentReport:
    """Run all checks and return an :class:`EnvironmentReport`. Never raises."""
    with span("detect.environment") as s:
        settings = get_settings()
        profile = settings.profile_path
        try:
            initialized = profile.is_dir() and any(profile.iterdir())
        except OSError:
            initialized = False
        uia = (
            detect_package("uiautomation")
            if sys.platform == "win32"
            else PackageInfo(name="uiautomation", importable=False, reason="Windows-only")
        )
        report = EnvironmentReport(
            os=current_os(),
            os_version=platform.platform(),
            python_version=platform.python_version(),
            instagram_app=detect_instagram_app(check_window=check_window),
            browsers=find_browsers(),
            ffmpeg=detect_tool("ffmpeg"),
            ffprobe=detect_tool("ffprobe"),
            playwright=detect_package("playwright"),
            uiautomation=uia,
            browser_profile_dir=str(profile),
            browser_profile_initialized=initialized,
        )
        s.set(ok=report.ok, critical_missing=report.critical_missing)
        return report
