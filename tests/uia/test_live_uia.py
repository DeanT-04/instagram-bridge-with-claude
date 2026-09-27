"""Live, read-only tests against the real Microsoft Store Instagram app window.

Run with ``uv run pytest -q -m live tests/uia -s`` (``-s`` shows timings). Skipped when
not on Windows or when the app window is not open. These tests never press write
controls (Like/Follow/Save/Comment/Send/...), only navigate, scroll, read and capture.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(sys.platform != "win32", reason="Windows only"),
]


def _app_open() -> bool:
    if sys.platform != "win32":
        return False
    try:
        from heliograph.drivers.uia import app
        from heliograph.drivers.uia.runtime import UiaWorker

        worker = UiaWorker()
        try:
            return bool(worker.call(app.find_app_windows))
        finally:
            worker.shutdown()
    except Exception:
        return False


@pytest.fixture
async def driver() -> AsyncIterator[object]:
    if not _app_open():
        pytest.skip("Instagram Store app window is not open")
    from heliograph.drivers.uia import UiaDriver

    d = UiaDriver(launch=False)
    await d.ensure_ready()
    yield d
    await d.close()


def _all_exist(paths: list[str]) -> bool:
    return all(os.path.exists(p) for p in paths)


def _timed(label: str, t0: float) -> None:
    print(f"[timing] {label}: {(time.perf_counter() - t0) * 1000:.0f} ms")


async def test_live_snapshot(driver) -> None:  # type: ignore[no-untyped-def]
    t0 = time.perf_counter()
    snap = await driver.take_snapshot()
    _timed(f"snapshot ({len(snap)} nodes / {snap.raw_count} raw)", t0)
    assert snap.url and snap.url.startswith("https://www.instagram.com/")
    assert len(snap) > 20 and snap.took_ms < 3000
    text = snap.render_text(interactive_only=True)
    assert "[e" in text
    data = await driver.snapshot(interactive_only=True, visible_only=True, max_nodes=50)
    assert data["driver"] == "uia" and 0 < len(data["nodes"]) <= 50
    assert await driver.current_url() == snap.url


async def test_live_screenshot(driver, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from PIL import Image

    t0 = time.perf_counter()
    out = await driver.screenshot(tmp_path / "app.png")
    _timed("screenshot", t0)
    with Image.open(out) as img:
        assert img.width > 300 and img.height > 200
        assert img.getbbox() is not None


async def test_live_navigation_roundtrip(driver) -> None:  # type: ignore[no-untyped-def]
    for section, expected in (
        ("reels", {"reels", "reel"}),
        ("home", {"home"}),
        ("profile", {"own_profile"}),
        ("home", {"home"}),
    ):
        t0 = time.perf_counter()
        result = await driver.navigate_section(section)
        _timed(f"navigate_section({section}) via {result['method']}", t0)
        assert await driver.current_section() in expected, result


async def test_live_visible_posts_and_scroll(driver) -> None:  # type: ignore[no-untyped-def]
    if await driver.current_section() != "home":
        await driver.navigate_section("home")
    await driver.wait_settled()
    t0 = time.perf_counter()
    posts = await driver.visible_posts()
    _timed(f"visible_posts ({len(posts)})", t0)
    assert posts and posts[0]["author"]
    assert {"like", "save"} <= set(posts[0]["refs"]) or posts[0]["refs"].get("author")
    t0 = time.perf_counter()
    assert await driver.scroll("down") == "document"  # feed scrolls via ScrollPattern
    assert await driver.scroll("up") == "document"
    _timed("scroll down+up", t0)
    badges = await driver.unread_badges()
    assert set(badges) == {"messages", "notifications"}


async def test_live_failure_attaches_screenshot(driver, recorded) -> None:  # type: ignore[no-untyped-def]
    from heliograph.errors import ElementNotFoundError

    with pytest.raises(ElementNotFoundError):
        await driver.click(name="__no_such_control__")
    event = recorded(name="uia.click")[-1]
    assert event["status"] == "error"
    kinds = {a["kind"] for a in event["artifacts"]}
    assert {"screenshot", "uia_snapshot"} <= kinds
    assert _all_exist([a["path"] for a in event["artifacts"]])


async def test_live_navigate_url(driver) -> None:  # type: ignore[no-untyped-def]
    """In-place link navigation when the page links the URL, else a replacement window."""
    t0 = time.perf_counter()
    first = await driver.navigate_url("https://www.instagram.com/explore/")
    _timed(f"navigate_url(explore) via {first['method']}", t0)
    assert first["method"] in ("link", "noop")
    t0 = time.perf_counter()
    other = await driver.navigate_url("https://www.instagram.com/instagram/")
    _timed(f"navigate_url(/instagram/) via {other['method']}", t0)
    assert other["method"] in ("link", "new_window")
    assert (await driver.current_url()).startswith("https://www.instagram.com/instagram/")
    await driver.navigate_section("home")
    assert await driver.current_section() == "home"


async def test_live_search_typing(driver) -> None:  # type: ignore[no-untyped-def]
    """Type into the search box (read-only) and read the results."""
    await driver.navigate_section("search")
    t0 = time.perf_counter()
    await driver.type_text("natgeo", name="Search input", clear=True)
    for _ in range(20):  # results arrive over the network: allow ~8 s
        snap = await driver.take_snapshot()
        if any(n.value.rstrip("/").endswith("/natgeo") for n in snap.nodes):
            break
        await asyncio.sleep(0.4)
    _timed("search type + results", t0)
    assert any(n.value.rstrip("/").endswith("/natgeo") for n in snap.nodes)
    await driver.type_text("", name="Search input", clear=True)
    await driver.navigate_section("home")
