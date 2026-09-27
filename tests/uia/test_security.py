"""Security regressions for the UIA driver: write-control refusal and app launch URLs."""

from __future__ import annotations

from typing import Any

import pytest

from heliograph.drivers.uia import UiaDriver, app
from heliograph.drivers.uia.tree import build_snapshot
from heliograph.drivers.uia.writes import WriteActions, is_write_control, normalize_control_name
from heliograph.errors import UnsafeActionError
from tests.uia.fakes import btn, group, home_tree, n, snap


class _NoWorker:
    async def run(self, *a: Any, **k: Any) -> Any:
        raise AssertionError("UIA must not be touched")

    def shutdown(self) -> None:
        pass


@pytest.fixture
def driver() -> UiaDriver:
    d = UiaDriver()
    d._worker = _NoWorker()  # type: ignore[assignment]
    d._snap = snap(home_tree())
    WriteActions._last_write = 0.0
    return d


@pytest.mark.parametrize(
    "name",
    ["LIKE", "like", " Like ", "Like\u200b", "\uff2c\uff49\uff4b\uff45", "Follow back",
     "Unfollow alice", "Delete comment", "Report post", "Block", "Log out", "Requested"],
)
def test_write_names_are_normalised(name: str) -> None:
    s = build_snapshot(group(n("button", name), name="root"))
    node = next(x for x in s.nodes if x.role == "button")
    assert is_write_control(node), normalize_control_name(name)


@pytest.mark.parametrize("name", ["Home", "Saved", "Posts", "Followers", "More Options",
                                  "Comment", "Reels", "Liked by alice"])
def test_read_names_are_not_write_controls(name: str) -> None:
    s = build_snapshot(group(n("button", name), name="root"))
    node = next(x for x in s.nodes if x.role == "button")
    assert not is_write_control(node)


async def test_click_refuses_icon_inside_write_button(driver: UiaDriver) -> None:
    # btn("Unlike", image "Like"): the image survives compaction and a click on it
    # (mouse fallback) would press the Unlike button.
    images = driver._snap.find("Like", "image")  # type: ignore[union-attr]
    assert images, "fake tree should expose the icon image"
    with pytest.raises(UnsafeActionError):
        await driver.click(images[0].ref)


async def test_click_refuses_any_role_named_like_a_write(driver: UiaDriver) -> None:
    driver._snap = build_snapshot(group(n("text", "Follow"), btn("Home"), name="root"))
    ref = driver._snap.find("Follow")[0].ref
    with pytest.raises(UnsafeActionError):
        await driver.click(ref)


@pytest.mark.parametrize(
    "url",
    [
        "--remote-debugging-port=9333",
        "https://www.instagram.com/ --remote-debugging-port=9333",
        'https://www.instagram.com/" --remote-debugging-port=9333 "',
        "https://www.instagram.com/\t--x",
        "https://evil.com/",
        "https://www.instagram.com.evil.com/",
        "https://www.instagram.com:9333/",
        "file:///C:/Windows/",
    ],
)
def test_app_launch_refuses_unsafe_urls(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    popen: list[Any] = []
    monkeypatch.setattr(app, "edge_executable", lambda: "msedge.exe")
    monkeypatch.setattr(app.subprocess, "Popen", lambda *a, **k: popen.append(a))
    with pytest.raises(UnsafeActionError):
        app.launch(url)
    assert popen == []


def test_app_launch_passes_url_as_single_argument(monkeypatch: pytest.MonkeyPatch) -> None:
    popen: list[Any] = []
    monkeypatch.setattr(app, "edge_executable", lambda: "msedge.exe")
    monkeypatch.setattr(app.subprocess, "Popen", lambda cmd, **k: popen.append(cmd))
    app.launch("https://www.instagram.com/reels/")
    assert popen[0][-1] == "--app-launch-url-for-shortcuts-menu-item=https://www.instagram.com/reels/"
    assert not any("remote-debugging" in a for a in popen[0])


async def test_navigate_refuses_parser_differential_urls(driver: UiaDriver) -> None:
    with pytest.raises(UnsafeActionError):
        await driver.navigate("https://www.instagram.com/ x")
