"""Security regressions for the CDP driver (URL/path allow-lists, endpoint ownership)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from heliograph.config import get_settings
from heliograph.drivers.cdp import devtools
from heliograph.drivers.cdp.devtools import BrowserProcess, CdpEndpoint
from heliograph.drivers.cdp.launcher import BrowserLauncher
from heliograph.drivers.cdp.ratelimit import RateLimiter
from heliograph.drivers.cdp.session import is_instagram_url
from heliograph.drivers.cdp.webapi import InstagramApiError, WebApiClient, build_url

BS = "\\"


@pytest.mark.parametrize(
    "url",
    [
        f"https://evil.com{BS}@www.instagram.com/",  # Chromium: host evil.com
        f"https://www.instagram.com{BS}.evil.com/",
        "https://user:pw@www.instagram.com/",
        "https://www.instagram.com@evil.com/",
        "https://www.instagram.com:8443/",
        "https://www.instagram.com/\t",
        "https://www.instagram.com/ x",
        "https://www.instagram.com/\u2025/",
        "HTTPS://evil.com/",
    ],
)
def test_is_instagram_url_rejects_parser_differentials(url: str) -> None:
    assert is_instagram_url(url) is False


def test_is_instagram_url_still_accepts_normal_urls() -> None:
    assert is_instagram_url("https://www.instagram.com/reel/DAbc_1-x/?igsh=1")
    assert is_instagram_url("https://WWW.Instagram.com:443/p/x/")


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/../../accounts/logout/",
        "/api/v1/%2e%2e/%2E%2e/accounts/",
        "/api/v1/x/.%2e/y",
        "/api/v1/./x",
        "/api/v1/a%2fb/",
        "/api/v1/a%5c..%5c/",
        f"/api/v1/a{BS}b/",
        "/api/v1/a\t/",
        "/api/v1/a\n/",
        "/api/v1/x#frag",
        "/api/v1/\u00e9/",
    ],
)
def test_build_url_rejects_traversal_and_encoded_escapes(path: str) -> None:
    with pytest.raises(ValueError):
        build_url(path)


def test_build_url_params_cannot_inject_path() -> None:
    url = build_url("/api/v1/x/", {"q": "../../evil#x"})
    assert url == "/api/v1/x/?q=..%2F..%2Fevil%23x"


class _ForeignPage:
    async def evaluate(self, expression: str, arg: Any = None) -> Any:
        assert "location.origin" in expression
        return {"status": 0, "wrongOrigin": "https://evil.com", "url": "", "body": ""}


async def test_api_refuses_when_page_left_instagram() -> None:
    api = WebApiClient(_ForeignPage(), read_limiter=RateLimiter(0, 0),
                       write_limiter=RateLimiter(0, 0))
    with pytest.raises(InstagramApiError, match=r"evil\.com"):
        await api.get("/api/v1/feed/saved/posts/")


# -- endpoint ownership -------------------------------------------------------------
def _ep(port: int, path: str = "/devtools/browser/x") -> CdpEndpoint:
    return CdpEndpoint(port=port, ws_url=f"ws://127.0.0.1:{port}{path}", browser="E")


def test_state_port_needs_process_confirmation(
    monkeypatch: pytest.MonkeyPatch, isolated_home: Path
) -> None:
    # Our browser is gone; some other DevTools server now answers on the remembered port.
    devtools.write_state(get_settings().state_file, cdp={"default": {"port": 9600}})
    monkeypatch.setattr(devtools, "find_browser_process", lambda p: None)
    monkeypatch.setattr(devtools, "probe_endpoint", lambda port, timeout=2.0: _ep(port))
    assert BrowserLauncher().find_running() is None


def test_state_port_rejected_when_process_is_on_other_port(
    monkeypatch: pytest.MonkeyPatch, isolated_home: Path
) -> None:
    devtools.write_state(get_settings().state_file, cdp={"default": {"port": 9600}})
    monkeypatch.setattr(devtools, "find_browser_process", lambda p: BrowserProcess(5, 9700))
    monkeypatch.setattr(devtools, "probe_endpoint", lambda port, timeout=2.0: _ep(port))
    ep = BrowserLauncher().find_running()
    assert ep is not None and ep.port == 9700


def test_stale_devtools_active_port_with_other_browser_is_rejected(
    monkeypatch: pytest.MonkeyPatch, isolated_home: Path
) -> None:
    prof = BrowserLauncher().profile_dir
    prof.mkdir(parents=True)
    (prof / "DevToolsActivePort").write_text("9500\n/devtools/browser/ours\n")
    monkeypatch.setattr(devtools, "find_browser_process", lambda p: None)
    monkeypatch.setattr(devtools, "probe_endpoint",
                        lambda port, timeout=2.0: _ep(port, "/devtools/browser/theirs"))
    assert BrowserLauncher().find_running() is None
    monkeypatch.setattr(devtools, "probe_endpoint",
                        lambda port, timeout=2.0: _ep(port, "/devtools/browser/ours"))
    ep = BrowserLauncher().find_running()
    assert ep is not None and ep.port == 9500


def test_process_scan_requires_browser_executable() -> None:
    prof = Path(r"C:\Users\me\.heliograph\browser-profile")
    tail = f' --user-data-dir="{prof}" --remote-debugging-port=9334'
    assert devtools.parse_cmdline_port(r'"C:\Edge\msedge.exe"' + tail, prof) == 9334
    assert devtools.parse_cmdline_port(r"C:\Chrome\chrome.exe" + tail, prof) == 9334
    assert devtools.parse_cmdline_port(
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" + tail, prof) == 9334
    assert devtools.parse_cmdline_port(r'"C:\Temp\evil.exe"' + tail, prof) is None
    assert devtools.parse_cmdline_port(r"python.exe fake.py" + tail, prof) is None


def test_probe_rejects_non_local_websocket(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, timeout: float) -> httpx.Response:
        return httpx.Response(200, json={"Browser": "x",
                                         "webSocketDebuggerUrl": "ws://evil.com:9222/devtools/x"})

    monkeypatch.setattr(devtools.httpx, "get", fake_get)
    assert devtools.probe_endpoint(9222) is None

    def good_get(url: str, timeout: float) -> httpx.Response:
        return httpx.Response(200, json={
            "Browser": "x", "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/a"})

    monkeypatch.setattr(devtools.httpx, "get", good_get)
    ep = devtools.probe_endpoint(9222)
    assert ep is not None and ep.port == 9222
