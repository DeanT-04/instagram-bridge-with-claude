"""Live discovery of the running Heliograph browser (read-only; never launches or closes)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from heliograph.config import get_settings
from heliograph.drivers.cdp import BrowserLauncher, CdpDriver
from heliograph.errors import DriverUnavailableError

pytestmark = pytest.mark.live


@pytest.fixture
def real_profile(isolated_home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    profile = Path(os.environ.get("HELIOGRAPH_LIVE_PROFILE") or
                   Path.home() / ".heliograph" / "browser-profile")
    monkeypatch.setenv("HELIOGRAPH_BROWSER_PROFILE_DIR", str(profile))
    get_settings.cache_clear()
    return profile


def test_find_running_browser(real_profile: Path) -> None:
    ep = BrowserLauncher().find_running()
    if ep is None:
        pytest.skip("No Heliograph browser running")
    assert ep.host == "127.0.0.1" and ep.ws_url.startswith("ws://127.0.0.1:")
    assert get_settings().state_file.is_file()


async def test_capture_and_login_state(real_profile: Path) -> None:
    drv = CdpDriver(launch=False)
    try:
        await drv.connect(create_page=False)
    except DriverUnavailableError as exc:
        pytest.skip(str(exc))
    try:
        assert await drv.is_logged_in()
        assert (await drv.viewer_id() or "").isdigit()
        async with drv.capture() as cap:
            await drv.api().get("/api/v1/web/search/topsearch/", {"query": "instagram"})
        # fetches issued via page.evaluate are page responses too
        assert any(r.path.endswith("/topsearch/") for r in cap.responses)
    finally:
        await drv.close()
