"""Detection (and installation hand-off) of the Microsoft Store Instagram app."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

from heliograph.detect.models import (
    INSTAGRAM_AUMID,
    INSTAGRAM_PACKAGE,
    INSTAGRAM_STORE_PRODUCT_ID,
    InstagramAppInfo,
)
from heliograph.eye import span

__all__ = ["detect_instagram_app", "find_instagram_window", "install_instagram_app"]

STORE_URI = f"ms-windows-store://pdp/?ProductId={INSTAGRAM_STORE_PRODUCT_ID}"
_PS_QUERY = (
    f"Get-AppxPackage -Name {INSTAGRAM_PACKAGE} | "
    "Select-Object Name,Version,PackageFamilyName,InstallLocation | ConvertTo-Json -Compress"
)


def _powershell_exe() -> str:
    """Absolute path to Windows PowerShell.

    A bare ``"powershell"`` is resolved by ``CreateProcess``, which searches the current
    directory before System32 - a ``powershell.exe`` planted there would be executed.
    """
    root = os.environ.get("SYSTEMROOT") or ""
    if not os.path.isabs(root):
        root = r"C:\Windows"
    return os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")


def _run_powershell(command: str, timeout: float = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_powershell_exe(), "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _app_windows() -> list[Any]:
    """Store-app windows via the UIA driver's AUMID matcher (on a COM-initialised thread)."""
    from heliograph.drivers.uia import app
    from heliograph.drivers.uia.runtime import UiaWorker

    worker = UiaWorker()
    try:
        return list(worker.call(app.find_app_windows))
    finally:
        worker.shutdown()


def find_instagram_window() -> tuple[bool | None, str | None, str | None]:
    """Look for an open window of the Store Instagram app.

    Returns ``(open, title, reason)``; ``open`` is None when the check is impossible.
    Reuses :func:`heliograph.drivers.uia.app.find_app_windows`, which matches top-level
    windows by the app's AppUserModelID, so an Edge/Chrome tab (or Heliograph's own CDP
    window) titled "Instagram" is not mistaken for the app.
    """
    if sys.platform != "win32":
        return None, None, "not Windows"
    with span("detect.instagram_window") as s:
        try:
            windows = _app_windows()
        except ImportError as exc:
            s.set(reason="pywin32/uiautomation not importable")
            return None, None, f"window check unavailable: {exc}"
        except Exception as exc:
            return None, None, f"window check failed: {exc}"
        s.set(found=bool(windows), count=len(windows))
        if windows:
            return True, str(windows[0].title), None
        return False, None, None


def _parse_package(stdout: str) -> dict[str, Any] | None:
    text = stdout.strip()
    if not text:
        return None
    data = json.loads(text)
    if isinstance(data, list):  # several versions registered: take the first
        data = data[0] if data else None
    return data if isinstance(data, dict) else None


def detect_instagram_app(*, check_window: bool = True) -> InstagramAppInfo:
    """Detect the Store Instagram app. Never raises; failures are reported in ``reason``."""
    if sys.platform != "win32":
        return InstagramAppInfo(supported=False, reason="Store app is Windows-only; web used")
    with span("detect.instagram_app") as s:
        try:
            proc = _run_powershell(_PS_QUERY)
            if proc.returncode != 0:
                s.set(returncode=proc.returncode)
                return InstagramAppInfo(
                    supported=True, reason=f"Get-AppxPackage failed: {proc.stderr.strip()[:200]}"
                )
            pkg = _parse_package(proc.stdout)
        except FileNotFoundError:
            return InstagramAppInfo(supported=True, reason="powershell not found")
        except subprocess.TimeoutExpired:
            return InstagramAppInfo(supported=True, reason="Get-AppxPackage timed out")
        except (json.JSONDecodeError, OSError) as exc:
            return InstagramAppInfo(supported=True, reason=f"could not query package: {exc}")
        if pkg is None:
            s.set(installed=False)
            return InstagramAppInfo(supported=True, installed=False, reason="not installed")
        family = pkg.get("PackageFamilyName")
        info = InstagramAppInfo(
            supported=True,
            installed=True,
            name=pkg.get("Name"),
            version=str(pkg.get("Version")) if pkg.get("Version") is not None else None,
            package_family_name=family,
            aumid=f"{family}!App" if family else INSTAGRAM_AUMID,
            install_location=pkg.get("InstallLocation"),
        )
        s.set(installed=True, version=info.version)
    if check_window:
        info.window_open, info.window_title, reason = find_instagram_window()
        if reason:
            info.reason = reason
    return info


def install_instagram_app() -> str:
    """Start installing the Instagram app, returning a message for the user. Never raises.

    On Windows this opens the Microsoft Store product page; elsewhere it explains that the
    Instagram web app (CDP driver) will be used instead.
    """
    if sys.platform != "win32":
        return (
            "The Instagram desktop app is only available on Windows. Heliograph will use the "
            "Instagram web app in a dedicated browser window instead (run `heliograph login`)."
        )
    with span("detect.install_instagram_app", uri=STORE_URI):
        try:
            os.startfile(STORE_URI)  # type: ignore[attr-defined,unused-ignore]
        except OSError as exc:
            return f"Could not open the Microsoft Store ({exc}). Open manually: {STORE_URI}"
    return "Opened the Microsoft Store page for Instagram. Click 'Get', then re-run doctor."
