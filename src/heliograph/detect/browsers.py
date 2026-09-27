"""Locate Chromium-family browsers (Edge, Chrome, Chromium) on Windows, macOS and Linux."""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable
from pathlib import PureWindowsPath

from heliograph.detect.models import BrowserInfo
from heliograph.eye import span

__all__ = ["BROWSER_ORDER", "find_browsers", "registry_app_path"]

BROWSER_ORDER = ("msedge", "chrome", "chromium")

_WIN_EXE = {"msedge": "msedge.exe", "chrome": "chrome.exe", "chromium": "chromium.exe"}
_WIN_REL = {
    "msedge": [r"Microsoft\Edge\Application\msedge.exe"],
    "chrome": [r"Google\Chrome\Application\chrome.exe"],
    "chromium": [r"Chromium\Application\chrome.exe"],
}
_MAC_PATHS = {
    "msedge": ["/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"],
    "chrome": ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"],
    "chromium": ["/Applications/Chromium.app/Contents/MacOS/Chromium"],
}
_LINUX_NAMES = {
    "msedge": ["microsoft-edge", "microsoft-edge-stable"],
    "chrome": ["google-chrome", "google-chrome-stable"],
    "chromium": ["chromium", "chromium-browser"],
}
_LINUX_PATHS = {
    "msedge": ["/opt/microsoft/msedge/msedge"],
    "chrome": ["/opt/google/chrome/chrome"],
    "chromium": ["/snap/bin/chromium", "/usr/lib/chromium/chromium"],
}


def registry_app_path(exe: str) -> str | None:
    """Read ``HKLM/HKCU\\...\\App Paths\\<exe>`` default value (Windows only)."""
    if sys.platform != "win32":
        return None
    import winreg

    key = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, key) as handle:
                value, _ = winreg.QueryValueEx(handle, "")
                if value:
                    return str(value).strip('"')
        except OSError:
            continue
    return None


def _windows_candidates(channel: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    reg = registry_app_path(_WIN_EXE[channel])
    if reg:
        out.append((reg, "registry"))
    roots = [os.environ.get(v) for v in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA")]
    for root in filter(None, roots):
        for rel in _WIN_REL[channel]:
            out.append((str(PureWindowsPath(root) / rel), "common-location"))
    return out


def _posix_candidates(channel: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if sys.platform == "darwin":
        out += [(p, "common-location") for p in _MAC_PATHS[channel]]
    for name in _LINUX_NAMES[channel]:
        found = shutil.which(name)
        if found:
            out.append((found, "path"))
    if sys.platform.startswith("linux"):
        out += [(p, "common-location") for p in _LINUX_PATHS[channel]]
    return out


def find_browsers(*, exists: Callable[[str], bool] = os.path.isfile) -> list[BrowserInfo]:
    """Return detected browsers ordered Edge, Chrome, Chromium (one entry each). Never raises."""
    found: list[BrowserInfo] = []
    with span("detect.browsers") as s:
        for channel in BROWSER_ORDER:
            try:
                cands = (
                    _windows_candidates(channel)
                    if sys.platform == "win32"
                    else _posix_candidates(channel)
                )
                for path, source in cands:
                    if exists(path):
                        found.append(BrowserInfo(channel=channel, path=path, source=source))
                        break
            except Exception as exc:  # keep going with other channels
                s.set(**{f"{channel}_error": repr(exc)})
        s.set(found=[b.channel for b in found])
    return found
