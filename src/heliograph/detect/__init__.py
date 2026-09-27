"""Environment detection: OS, Store Instagram app, browsers, ffmpeg, Python packages.

Every check is wrapped in an eye span and never raises; problems surface as fields
(``found=False``, ``reason=...``) on :class:`EnvironmentReport`. The independent checks run
concurrently (each is mostly waiting on a subprocess), so a full report costs about as
long as the slowest check rather than the sum of all of them.
"""

from __future__ import annotations

import contextvars
import json
import platform
import sys
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import TypeVar

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
    "last_login_state",
]

T = TypeVar("T")


def current_os() -> str:
    """Return ``windows``, ``macos``, ``linux`` or ``other``."""
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    return "other"


def last_login_state(account: str = "default") -> bool | None:
    """Login state of ``account``'s browser profile as last observed by Heliograph.

    Recorded in ``state.json`` whenever the CDP driver checks the session; None when it has
    never been checked. Cheap (no browser needed), but it can be stale.
    """
    try:
        state = json.loads(get_settings().state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    cdp = state.get("cdp") if isinstance(state, dict) else None
    entry = cdp.get(account) if isinstance(cdp, dict) else None
    value = entry.get("logged_in") if isinstance(entry, dict) else None
    return value if isinstance(value, bool) else None


def _uiautomation() -> PackageInfo:
    if sys.platform == "win32":
        return detect_package("uiautomation")
    return PackageInfo(name="uiautomation", importable=False, reason="Windows-only")


def detect_environment(*, check_window: bool = True) -> EnvironmentReport:
    """Run all checks (concurrently) and return an :class:`EnvironmentReport`. Never raises."""
    with span("detect.environment") as s:
        settings = get_settings()
        profile = settings.profile_path
        try:
            initialized = profile.is_dir() and any(profile.iterdir())
        except OSError:
            initialized = False
        with ThreadPoolExecutor(max_workers=6, thread_name_prefix="detect") as pool:

            def run(fn: Callable[[], T]) -> Future[T]:
                # copy_context: each check's eye span nests under detect.environment
                return pool.submit(contextvars.copy_context().run, fn)

            app = run(lambda: detect_instagram_app(check_window=check_window))
            browsers = run(find_browsers)
            ffmpeg = run(lambda: detect_tool("ffmpeg"))
            ffprobe = run(lambda: detect_tool("ffprobe"))
            playwright = run(lambda: detect_package("playwright"))
            uia = run(_uiautomation)
            report = EnvironmentReport(
                os=current_os(),
                os_version=platform.platform(),
                python_version=platform.python_version(),
                instagram_app=app.result(),
                browsers=browsers.result(),
                ffmpeg=ffmpeg.result(),
                ffprobe=ffprobe.result(),
                playwright=playwright.result(),
                uiautomation=uia.result(),
                browser_profile_dir=str(profile),
                browser_profile_initialized=initialized,
                logged_in=last_login_state(),
            )
        s.set(ok=report.ok, critical_missing=report.critical_missing)
        return report
