"""Live read-only checks against the running, logged-in Heliograph browser profile.

Run with ``uv run pytest -m live tests/cdp tests/instagram``. Skips when no browser with
DevTools is running on the profile or no Instagram window is open. Never performs writes.
Point at another profile with ``HELIOGRAPH_LIVE_PROFILE``.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from heliograph.config import get_settings
from heliograph.drivers.cdp import CdpDriver
from heliograph.drivers.cdp.webapi import InstagramApiError
from heliograph.errors import DriverUnavailableError, RateLimitedError
from heliograph.instagram.collections import CollectionLister
from heliograph.instagram.service import InstagramService

pytestmark = pytest.mark.live


@pytest.fixture
async def driver(isolated_home: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[CdpDriver]:
    profile = os.environ.get("HELIOGRAPH_LIVE_PROFILE") or str(
        Path.home() / ".heliograph" / "browser-profile")
    monkeypatch.setenv("HELIOGRAPH_BROWSER_PROFILE_DIR", profile)
    get_settings.cache_clear()
    drv = CdpDriver(launch=False)
    try:
        await drv.connect(create_page=False)
    except DriverUnavailableError as exc:
        pytest.skip(f"CDP browser not available: {exc}")
    if not await drv.is_logged_in():
        await drv.close()
        pytest.skip("Heliograph profile is not logged in")
    yield drv
    await drv.close()


@pytest.fixture
async def svc(driver: CdpDriver) -> InstagramService:
    return InstagramService(driver.api(), collections=CollectionLister(driver),
                            viewer_id=await driver.viewer_id())


async def test_snapshot_is_compact(driver: CdpDriver, tmp_path: Path) -> None:
    snap = await driver.snapshot()
    assert snap["driver"] == "cdp" and "instagram.com" in snap["url"]
    assert len(str(snap)) < 60_000 and snap["nodes"]
    shot = await driver.screenshot(tmp_path / "s.png")
    assert shot.stat().st_size > 1000


async def test_viewer(svc: InstagramService) -> None:
    me = await svc.viewer()
    assert me.username and me.pk


async def test_saved_and_media_roundtrip(svc: InstagramService) -> None:
    page = await svc.saved_medias()
    assert page.items, "expected at least one saved post"
    first = page.items[0]
    assert first.code and first.pk
    again = await svc.media(first.code)  # exercises shortcode -> pk conversion
    assert again.pk == first.pk
    comments = await svc.comments(first.pk)
    assert isinstance(comments.items, list)


async def test_collections_and_iteration(svc: InstagramService) -> None:
    cols = await svc.list_collections()
    assert cols, "expected at least one saved collection"
    wanted = os.environ.get("HELIOGRAPH_LIVE_COLLECTION") or cols[0].name
    items = [m async for m in svc.iter_collection(wanted, limit=3)]
    assert items and all(m.pk for m in items)


async def test_search_and_user(svc: InstagramService) -> None:
    res = await svc.search("instagram")
    assert res.users
    try:
        user = await svc.get_user(res.users[0].username)
    except RateLimitedError:
        pytest.xfail("web_profile_info is rate-limited for this session")
    assert user.username == res.users[0].username
    page = await svc.user_medias(user.username, count=3)
    assert isinstance(page.items, list)


@pytest.mark.parametrize("op", ["timeline", "reels", "explore", "inbox", "activity"])
async def test_feeds(svc: InstagramService, op: str) -> None:
    try:
        result = await getattr(svc, op)()
    except InstagramApiError as exc:
        pytest.xfail(f"{op} endpoint not available on web: {exc}")
    items = result if isinstance(result, list) else result.items
    assert isinstance(items, list)
