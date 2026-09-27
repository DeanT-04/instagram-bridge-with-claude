"""Low-level helpers to discover and validate a Chromium DevTools endpoint.

Discovery order for an already-running browser that owns a given ``--user-data-dir``:

1. the ``DevToolsActivePort`` file Chromium writes into the profile (not always present:
   Edge only writes it for some launch modes);
2. the port recorded in Heliograph's ``state.json``;
3. the command lines of running browser processes (``--user-data-dir=<profile>`` together
   with ``--remote-debugging-port=<n>``).

Every candidate is validated with ``GET http://127.0.0.1:<port>/json/version`` before use.
"""

from __future__ import annotations

import json
import re
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

from heliograph.eye import span

__all__ = [
    "BrowserProcess",
    "CdpEndpoint",
    "find_browser_process",
    "free_port",
    "parse_cmdline_port",
    "probe_endpoint",
    "read_devtools_active_port",
    "read_state",
    "write_state",
]

HOST = "127.0.0.1"
_PORT_RE = re.compile(r"--remote-debugging-port=(\d+)")
_UDD_RE = re.compile(r"--user-data-dir=(?:\"([^\"]+)\"|(\S+))")


@dataclass(frozen=True)
class CdpEndpoint:
    """A validated DevTools endpoint."""

    port: int
    ws_url: str
    browser: str
    host: str = HOST

    @property
    def http_url(self) -> str:
        """``http://host:port`` suitable for Playwright's ``connect_over_cdp``."""
        return f"http://{self.host}:{self.port}"


def read_devtools_active_port(profile_dir: Path) -> int | None:
    """Return the port from ``<profile>/DevToolsActivePort`` or None if absent/invalid."""
    try:
        first = (profile_dir / "DevToolsActivePort").read_text(encoding="utf-8").splitlines()[0]
        port = int(first.strip())
    except (OSError, IndexError, ValueError):
        return None
    return port if 0 < port < 65536 else None


def probe_endpoint(port: int, *, timeout: float = 2.0) -> CdpEndpoint | None:
    """Validate ``port`` by fetching ``/json/version``; None if nothing answers there."""
    with span("cdp.probe", port=port) as s:
        try:
            resp = httpx.get(f"http://{HOST}:{port}/json/version", timeout=timeout)
            data = resp.json() if resp.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            data = None
        if not isinstance(data, dict) or not data.get("webSocketDebuggerUrl"):
            s.set(ok=False)
            return None
        s.set(ok=True, browser=data.get("Browser"))
        return CdpEndpoint(port=port, ws_url=str(data["webSocketDebuggerUrl"]),
                           browser=str(data.get("Browser", "")))


def free_port() -> int:
    """Ask the OS for a free TCP port on 127.0.0.1."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((HOST, 0))
        return int(sock.getsockname()[1])


def _norm(path: str | Path) -> str:
    return str(Path(path)).rstrip("\\/").lower()


def parse_cmdline_port(cmdline: str, profile_dir: Path) -> int | None:
    """Return the debug port if ``cmdline`` is a browser using ``profile_dir``."""
    udd = _UDD_RE.search(cmdline)
    port = _PORT_RE.search(cmdline)
    if not udd or not port or "--type=" in cmdline:
        return None
    if _norm(udd.group(1) or udd.group(2)) != _norm(profile_dir):
        return None
    return int(port.group(1))


@dataclass(frozen=True)
class BrowserProcess:
    """A running browser main process bound to a profile."""

    pid: int
    port: int


def _process_cmdlines() -> list[tuple[int, str]]:
    if sys.platform == "win32":
        script = (
            "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "
            "'*--remote-debugging-port=*' } | "
            "ForEach-Object { \"$($_.ProcessId)|$($_.CommandLine)\" }"
        )
        cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", script]
    else:
        cmd = ["ps", "-eo", "pid=,args="]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    rows: list[tuple[int, str]] = []
    for line in out.stdout.splitlines():
        pid, _, rest = line.strip().replace("|", " ", 1).partition(" ")
        if pid.isdigit() and "remote-debugging-port" in rest:
            rows.append((int(pid), rest))
    return rows


def find_browser_process(profile_dir: Path) -> BrowserProcess | None:
    """Scan running processes for a browser main process on ``profile_dir`` with a debug port."""
    with span("cdp.scan_processes", profile=str(profile_dir)) as s:
        for pid, line in _process_cmdlines():
            port = parse_cmdline_port(line, profile_dir)
            if port:
                s.set(pid=pid, port=port)
                return BrowserProcess(pid=pid, port=port)
        s.set(pid=None)
        return None


def read_state(state_file: Path) -> dict[str, object]:
    """Load ``state.json`` (empty dict when missing or corrupt)."""
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_state(state_file: Path, **updates: object) -> None:
    """Merge ``updates`` into ``state.json`` atomically."""
    data = read_state(state_file)
    data.update(updates)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    tmp.replace(state_file)
