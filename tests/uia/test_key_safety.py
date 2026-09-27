"""Keystroke confirm-gating, the mouse-fallback size guard and lazy-render waiting."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from heliograph.drivers.uia import UiaDriver, actions
from heliograph.drivers.uia.tree import build_snapshot
from heliograph.drivers.uia.writes import is_compose_box, is_submit_keys, unsafe_key_reason
from heliograph.errors import RateLimitedError, UnsafeActionError
from tests.uia.fakes import IG, VIEW, btn, home_tree, loading_tree, n, snap


class _FakeWorker:
    """Runs snapshot functions, records UIA actions, reports a configurable focus."""

    def __init__(self, focused: tuple[str, str] | None = ("edit", "Search input")) -> None:
        self.focused = focused
        self.sent: list[tuple[str, Any]] = []

    async def run(self, fn: Any, *a: Any, **k: Any) -> Any:
        if fn is actions.focused_element:
            return self.focused
        if fn is actions.press_keys:
            self.sent.append(("keys", a[0]))
            return None
        if fn is actions.type_text:
            self.sent.append(("type", a[0]))
            return None
        if fn is actions.click:
            self.sent.append(("click", a[0]))
            return "invoke"
        return fn(*a, **k)

    def shutdown(self) -> None:
        pass


def _comment_page() -> Any:
    return build_snapshot(
        n(
            "document",
            "",
            n("edit", "Add a comment…"),
            n("edit", "Search input"),
            btn("Post"),
            value=IG + "p/X/",
            rect=VIEW,
        )
    )


@pytest.fixture
def driver(monkeypatch: pytest.MonkeyPatch) -> UiaDriver:
    d = UiaDriver()
    d._worker = _FakeWorker()  # type: ignore[assignment]
    d._snap = _comment_page()

    async def foreground() -> None:
        return None

    monkeypatch.setattr(d, "require_foreground", foreground)
    monkeypatch.setattr(d, "_snapshot_sync", lambda: d._snap)
    return d


def _worker(d: UiaDriver) -> _FakeWorker:
    assert isinstance(d._worker, _FakeWorker)
    return d._worker


@pytest.mark.parametrize(
    ("keys", "submit"),
    [
        ("{Enter}", True),
        ("{Ctrl}{Enter}", True),
        ("{ENTER 2}", True),
        ("{Return}", True),
        ("hello\nworld", True),
        ("{Esc}", False),
        ("{PageDown}", False),
        ("{Ctrl}a", False),
        ("plain text", False),
    ],
)
def test_is_submit_keys(keys: str, submit: bool) -> None:
    assert is_submit_keys(keys) is submit


def test_key_policy() -> None:
    assert is_compose_box("edit", "Add a comment…")
    assert is_compose_box("edit", "Message")
    assert is_compose_box("group", "Write a message...")
    assert is_compose_box("edit", "")  # unnamed text field: assume it can submit
    assert not is_compose_box("edit", "Search input")
    assert not is_compose_box("hyperlink", "Messages")
    assert unsafe_key_reason("{Enter}", ("edit", "Add a comment…"))
    assert unsafe_key_reason("{Ctrl}{Enter}", ("edit", "Message"))
    assert unsafe_key_reason("{Enter}", None)  # unknown focus: refuse
    assert unsafe_key_reason("{Enter}", ("button", "Like"))  # Enter presses a button
    assert unsafe_key_reason("{Space}", ("button", "Follow"))
    assert unsafe_key_reason(" ", ("button", "x"), write_control=True)
    assert unsafe_key_reason("{Enter}", ("edit", "Search input")) is None
    assert unsafe_key_reason("{Esc}", ("edit", "Add a comment…")) is None
    assert unsafe_key_reason("{Space}", ("button", "More")) is None


async def test_press_enter_in_comment_box_requires_confirm(driver: UiaDriver) -> None:
    _worker(driver).focused = ("edit", "Add a comment…")
    for keys in ("{Enter}", "{Ctrl}{Enter}", "{Shift}{Enter}"):
        with pytest.raises(UnsafeActionError):
            await driver.press(keys)
    assert _worker(driver).sent == []
    await driver.press("{Esc}")  # harmless keys still work
    await driver.press("{Enter}", confirm=True)
    assert _worker(driver).sent == [("keys", "{Esc}"), ("keys", "{Enter}")]
    with pytest.raises(RateLimitedError):  # a confirmed submit took the shared write slot
        await driver.press("{Enter}", confirm=True)


async def test_press_enter_in_search_box_is_allowed(driver: UiaDriver) -> None:
    await driver.press("{Enter}")
    assert _worker(driver).sent == [("keys", "{Enter}")]


async def test_press_refuses_unknown_focus_and_write_buttons(driver: UiaDriver) -> None:
    _worker(driver).focused = None
    with pytest.raises(UnsafeActionError):
        await driver.press("{Enter}")
    _worker(driver).focused = ("button", "Follow")
    with pytest.raises(UnsafeActionError):
        await driver.press("{Space}")
    assert _worker(driver).sent == []


async def test_type_submit_into_comment_box_requires_confirm(driver: UiaDriver) -> None:
    box = driver._snap.find("Add a comment…", "edit")[0].ref  # type: ignore[union-attr]
    with pytest.raises(UnsafeActionError):
        await driver.type_text("nice", box, submit=True)
    with pytest.raises(UnsafeActionError):  # a newline would submit too
        await driver.type_text("nice\n", box)
    with pytest.raises(UnsafeActionError):  # focused element (no target)
        _worker(driver).focused = ("edit", "Add a comment…")
        await driver.type_text("nice", submit=True)
    assert _worker(driver).sent == []
    await driver.type_text("draft only", box)  # typing without Enter is not a write
    await driver.type_text("nice", box, submit=True, confirm=True)
    assert _worker(driver).sent == [
        ("type", "draft only"),
        ("type", "nice"),
        ("keys", "{Enter}"),
    ]


async def test_type_submit_into_search_is_allowed(driver: UiaDriver) -> None:
    await driver.type_text("cats", name="Search input", submit=True)
    assert _worker(driver).sent == [("type", "cats"), ("keys", "{Enter}")]


def test_last_snapshot_is_public(driver: UiaDriver) -> None:
    assert driver.last_snapshot is driver._snap


@pytest.mark.parametrize(
    ("size", "allowed"),
    [((40, 40), True), ((600, 48), True), ((300, 900), True), ((600, 700), False)],
)
def test_mouse_fallback_refuses_containers(size: tuple[int, int], allowed: bool) -> None:
    if allowed:
        actions.check_mouse_target(*size, "x", allow_large=False)
    else:
        with pytest.raises(UnsafeActionError):
            actions.check_mouse_target(*size, "article", allow_large=False)
        actions.check_mouse_target(*size, "article", allow_large=True)


async def test_visible_posts_waits_for_lazy_render(
    driver: UiaDriver, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live bug: right after launch the document had 5 raw nodes, so app_visible_posts
    returned 0 posts although app_badges (URL based) already said "home"."""
    frames = [
        build_snapshot(n("document", "Instagram", btn("x"), value=IG, rect=VIEW)),
        snap(loading_tree()),
        snap(home_tree()),
    ]

    def next_frame() -> Any:
        driver._snap = frames.pop(0) if len(frames) > 1 else frames[0]
        return driver._snap

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(driver, "_snapshot_sync", next_frame)
    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    posts = await driver.visible_posts()
    assert [p["author"] for p in posts] == ["tqe_trades", "fio.na"]


async def test_visible_posts_does_not_wait_on_pages_without_posts(
    driver: UiaDriver, monkeypatch: pytest.MonkeyPatch
) -> None:
    inbox = build_snapshot(
        n(
            "document",
            "",
            *[btn(f"thread {i}") for i in range(20)],
            value=IG + "direct/inbox/",
            rect=VIEW,
        )
    )
    calls = 0

    def frame() -> Any:
        nonlocal calls
        calls += 1
        driver._snap = inbox
        return inbox

    monkeypatch.setattr(driver, "_snapshot_sync", frame)
    assert await driver.visible_posts() == [] and calls == 1
