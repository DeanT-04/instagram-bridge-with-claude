"""Launch (or reuse) a dedicated Chromium profile with DevTools bound to 127.0.0.1.

The browser runs as an ``--app=`` window so it looks like the Instagram app. One profile
directory exists per Instagram account: the default account uses
``settings.browser_profile_dir``; any other account ``<home>/profiles/<account>``.
The chosen port is recorded in ``<home>/state.json`` under ``"cdp"``.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from heliograph.config import Settings, get_settings
from heliograph.detect import find_browsers
from heliograph.drivers.cdp import devtools
from heliograph.drivers.cdp.devtools import CdpEndpoint
from heliograph.errors import BrowserNotFoundError, DriverUnavailableError
from heliograph.eye import span

__all__ = ["DEFAULT_ACCOUNT", "INSTAGRAM_URL", "BrowserLauncher", "find_browser_executable"]

DEFAULT_ACCOUNT = "default"
INSTAGRAM_URL = "https://www.instagram.com/"
_ACCOUNT_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _playwright_chromium() -> str | None:
    """Locate a Playwright-managed Chromium build, if one was installed."""
    base = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    root = Path(base) if base and base != "0" else (
        Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright" if sys.platform == "win32"
        else Path.home() / ("Library/Caches" if sys.platform == "darwin" else ".cache")
        / "ms-playwright"
    )
    rel = {
        "win32": "chrome-win/chrome.exe",
        "darwin": "chrome-mac/Chromium.app/Contents/MacOS/Chromium",
    }.get(sys.platform, "chrome-linux/chrome")
    for cand in sorted(root.glob(f"chromium-*/{rel}"), reverse=True):
        if cand.is_file():
            return str(cand)
    return None


def find_browser_executable(channel: str = "auto") -> tuple[str, str]:
    """Return ``(channel, path)`` of the browser to use.

    ``auto`` prefers Edge, then Chrome, then a system Chromium, then Playwright's Chromium.

    Raises:
        BrowserNotFoundError: nothing suitable is installed.
    """
    found = {b.channel: b.path for b in find_browsers()}
    order = ["msedge", "chrome", "chromium"] if channel == "auto" else [channel]
    for ch in order:
        if ch in found:
            return ch, found[ch]
    if channel in ("auto", "chromium"):
        pw = _playwright_chromium()
        if pw:
            return "chromium", pw
    raise BrowserNotFoundError(f"No browser found for channel {channel!r}")


@dataclass
class BrowserLauncher:
    """Start or attach to the dedicated browser for one account.

    Args:
        account: Account key (``"default"`` or a username-like identifier).
        settings: Settings to use (defaults to :func:`get_settings`).
        popen: Injected for tests; defaults to :class:`subprocess.Popen`.
    """

    account: str = DEFAULT_ACCOUNT
    settings: Settings = field(default_factory=get_settings)
    popen: Callable[..., object] = subprocess.Popen
    start_timeout: float = 30.0

    def __post_init__(self) -> None:
        if not _ACCOUNT_RE.match(self.account):
            raise ValueError(f"Invalid account name {self.account!r}")

    @property
    def profile_dir(self) -> Path:
        """User-data-dir for this account."""
        if self.account == DEFAULT_ACCOUNT:
            return self.settings.profile_path
        return self.settings.home / "profiles" / self.account

    def find_running(self) -> CdpEndpoint | None:
        """Return the endpoint of an already-running browser on this profile, if any."""
        with span("cdp.find_running", account=self.account) as s:
            candidates: list[tuple[str, int | None]] = [
                ("DevToolsActivePort", devtools.read_devtools_active_port(self.profile_dir)),
                ("state", self._state_port()),
            ]
            for source, port in candidates:
                ep = devtools.probe_endpoint(port) if port else None
                if ep and self._owned_by_profile(port, source):
                    s.set(source=source, port=port)
                    return self._record(ep)
            proc = devtools.find_browser_process(self.profile_dir)
            if proc:
                ep = devtools.probe_endpoint(proc.port)
                if ep:
                    s.set(source="process", port=proc.port)
                    return self._record(ep, pid=proc.pid)
            s.set(source=None)
            return None

    def _owned_by_profile(self, port: int | None, source: str) -> bool:
        # DevToolsActivePort lives inside the profile, so it is authoritative. A port from
        # state.json could have been reused by another browser; confirm via process scan
        # only when that scan is conclusive.
        if source == "DevToolsActivePort":
            return True
        proc = devtools.find_browser_process(self.profile_dir)
        return proc is None or proc.port == port

    def ensure_running(self) -> CdpEndpoint:
        """Reuse a running instance or launch a new one; returns a validated endpoint.

        Raises:
            BrowserNotFoundError: no browser installed.
            DriverUnavailableError: the browser did not expose DevTools in time.
        """
        existing = self.find_running()
        if existing:
            return existing
        return self.launch()

    def launch(self, url: str = INSTAGRAM_URL) -> CdpEndpoint:
        """Launch a new browser window for this profile (does not check for a running one)."""
        channel, exe = find_browser_executable(self.settings.browser_channel)
        port = self.settings.cdp_port or devtools.free_port()
        profile = self.settings.ensure_dir(self.profile_dir)
        args = [
            exe, f"--user-data-dir={profile}", f"--remote-debugging-port={port}",
            "--remote-debugging-address=127.0.0.1", "--no-first-run",
            "--no-default-browser-check", f"--app={url}",
        ]
        with span("cdp.launch", account=self.account, channel=channel, port=port) as s:
            flags = 0
            if sys.platform == "win32":
                flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            proc = self.popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              stdin=subprocess.DEVNULL, creationflags=flags, close_fds=True)
            deadline = time.monotonic() + self.start_timeout
            while time.monotonic() < deadline:
                ep = devtools.probe_endpoint(port, timeout=1.0)
                if ep:
                    s.set(browser=ep.browser)
                    return self._record(ep, pid=getattr(proc, "pid", None), channel=channel)
                time.sleep(0.5)
            raise DriverUnavailableError(
                f"{channel} did not open DevTools on 127.0.0.1:{port} within "
                f"{self.start_timeout:.0f}s",
                hint="Close other windows using this profile and retry.",
            )

    def stop(self) -> bool:
        """Close the browser for this profile. Returns True if a process was signalled."""
        with span("cdp.stop", account=self.account) as s:
            proc = devtools.find_browser_process(self.profile_dir)
            if proc is None:
                s.set(found=False)
                return False
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T"],
                               capture_output=True, check=False, timeout=15)
            else:
                os.kill(proc.pid, 15)
            self._forget()
            s.set(found=True, pid=proc.pid)
            return True

    # -- state.json -----------------------------------------------------------------
    def _accounts_state(self) -> dict[str, dict[str, object]]:
        cdp = devtools.read_state(self.settings.state_file).get("cdp")
        return dict(cdp) if isinstance(cdp, dict) else {}

    def _state_port(self) -> int | None:
        entry = self._accounts_state().get(self.account)
        port = entry.get("port") if isinstance(entry, dict) else None
        return port if isinstance(port, int) else None

    def _record(self, ep: CdpEndpoint, **extra: object) -> CdpEndpoint:
        accounts = self._accounts_state()
        prev = accounts.get(self.account)
        entry: dict[str, object] = dict(prev) if isinstance(prev, dict) else {}
        entry.update(port=ep.port, profile=str(self.profile_dir), browser=ep.browser,
                     updated=time.time(), **{k: v for k, v in extra.items() if v is not None})
        accounts[self.account] = entry
        devtools.write_state(self.settings.state_file, cdp=accounts)
        return ep

    def _forget(self) -> None:
        accounts = self._accounts_state()
        accounts.pop(self.account, None)
        devtools.write_state(self.settings.state_file, cdp=accounts)
