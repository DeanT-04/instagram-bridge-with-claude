"""Find, launch, focus and close the Microsoft Store Instagram app window (win32 side).

The Store app is an Edge PWA. Its top-level windows are ``Chrome_WidgetWin_1`` windows
whose *AppUserModelID* (window property store, ``PKEY_AppUserModel_ID``) is
``Facebook.InstagramBeta_8xx8rvfyw5nnt!App``. That ID is what distinguishes the Store app
from an Edge tab or Heliograph's own CDP app window titled "Instagram", which carry other
AUMIDs (``MSEdge...``/``Chrome``) — so matching is on the AUMID only, never on the title.

All functions here are synchronous and must run on the UIA worker thread
(:class:`heliograph.drivers.uia.runtime.UiaWorker`), since the property store is COM.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from typing import Any

from heliograph.errors import AppNotInstalledError, DriverUnavailableError

__all__ = [
    "APP_ID",
    "AUMID",
    "AppWindow",
    "activate",
    "close_window",
    "copy_placement",
    "edge_executable",
    "find_app_windows",
    "is_installed",
    "launch",
    "set_show_state",
    "wait_for_new_window",
]

AUMID = "Facebook.InstagramBeta_8xx8rvfyw5nnt!App"
APP_ID = "akpamiohjfcnimfljfndmaldlcfphjmp"
"""Edge web-app id of the Store Instagram PWA (``--app-id=`` on its msedge process)."""
WINDOW_CLASS = "Chrome_WidgetWin_1"


@dataclass(frozen=True, slots=True)
class AppWindow:
    """One top-level window of the Store Instagram app."""

    hwnd: int
    title: str
    pid: int
    rect: tuple[int, int, int, int]
    minimized: bool


def _window_aumid(hwnd: int) -> str | None:
    from win32com.propsys import propsys, pscon

    try:
        store = propsys.SHGetPropertyStoreForWindow(hwnd, propsys.IID_IPropertyStore)
        value = store.GetValue(pscon.PKEY_AppUserModel_ID).GetValue()
    except Exception:  # pywintypes.com_error for windows without a store
        return None
    return str(value) if value else None


def find_app_windows() -> list[AppWindow]:
    """Return visible Store-app windows, most recently activated first (z-order)."""
    import win32gui
    import win32process

    found: list[AppWindow] = []

    def _cb(hwnd: int, _: Any) -> bool:
        if not win32gui.IsWindowVisible(hwnd) or win32gui.GetClassName(hwnd) != WINDOW_CLASS:
            return True
        if _window_aumid(hwnd) != AUMID:
            return True
        found.append(
            AppWindow(
                hwnd=hwnd,
                title=win32gui.GetWindowText(hwnd),
                pid=win32process.GetWindowThreadProcessId(hwnd)[1],
                rect=tuple(win32gui.GetWindowRect(hwnd)),
                minimized=bool(win32gui.IsIconic(hwnd)),
            )
        )
        return True

    win32gui.EnumWindows(_cb, None)
    return found


def is_installed() -> bool:
    """True if the Store app package is registered for this user (via ``heliograph.detect``)."""
    from heliograph.detect import detect_instagram_app

    return bool(detect_instagram_app(check_window=False).installed)


def edge_executable() -> str:
    """Path of ``msedge.exe`` (the PWA host), or raise :class:`DriverUnavailableError`."""
    from heliograph.detect import find_browsers

    for browser in find_browsers():
        if browser.channel == "msedge":
            return browser.path
    for base in (os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES")):
        if base:
            path = os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe")
            if os.path.isfile(path):
                return path
    raise DriverUnavailableError("Microsoft Edge (host of the Instagram Store app) not found")


def launch(url: str | None = None) -> None:
    """Start a new window of the Store app, optionally opened at ``url``.

    Without ``url`` the app is activated through its AUMID (``shell:AppsFolder``), exactly
    like the Start menu does. With ``url`` Edge is asked to open the app via
    ``--app-launch-url-for-shortcuts-menu-item`` (the jump-list mechanism), which opens a
    *new* app window at that in-scope URL. Live-verified on Edge 154.
    """
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if url is None:
        if not is_installed():
            raise AppNotInstalledError("Instagram Store app (Facebook.InstagramBeta) not installed")
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{AUMID}"], creationflags=flags)
        return
    cmd = [
        edge_executable(),
        "--profile-directory=Default",
        f"--app-id={APP_ID}",
        f"--app-launch-url-for-shortcuts-menu-item={url}",
    ]
    subprocess.Popen(cmd, creationflags=flags, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_for_new_window(known: set[int], timeout: float = 20.0, poll: float = 0.25) -> AppWindow:
    """Wait until a Store-app window whose handle is not in ``known`` appears."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for win in find_app_windows():
            if win.hwnd not in known:
                return win
        time.sleep(poll)
    raise DriverUnavailableError(f"Instagram app window did not appear within {timeout:.0f}s")


def set_show_state(hwnd: int, state: str) -> None:
    """``state`` is ``"restore"``, ``"maximize"`` or ``"minimize"``."""
    import win32con
    import win32gui

    cmd = {
        "restore": win32con.SW_RESTORE,
        "maximize": win32con.SW_MAXIMIZE,
        "minimize": win32con.SW_MINIMIZE,
    }[state]
    win32gui.ShowWindow(hwnd, cmd)


def activate(hwnd: int) -> bool:
    """Restore (if minimised) and bring ``hwnd`` to the foreground. Returns success.

    Windows may refuse ``SetForegroundWindow`` from a background process; a harmless
    ALT key tap first satisfies the foreground-lock rule.
    """
    import win32api
    import win32con
    import win32gui

    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    try:
        win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
        win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        return False
    return bool(win32gui.GetForegroundWindow() == hwnd)


def copy_placement(src: int, dst: int) -> None:
    """Give window ``dst`` the position/size/show-state of ``src`` (best effort)."""
    import win32gui

    try:
        win32gui.SetWindowPlacement(dst, win32gui.GetWindowPlacement(src))
    except Exception:
        pass


def close_window(hwnd: int, timeout: float = 5.0) -> bool:
    """Ask the window to close (``WM_CLOSE``) and wait for it to go. Returns True if gone.

    Closing a PWA window loses nothing: the Instagram session lives in the Edge profile.
    """
    import win32con
    import win32gui

    if not win32gui.IsWindow(hwnd):
        return True
    win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not win32gui.IsWindow(hwnd):
            return True
        time.sleep(0.1)
    return False
