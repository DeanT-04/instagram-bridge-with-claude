"""Driver safety rules and helpers that need no real app window."""

from __future__ import annotations

from typing import Any

import pytest

from heliograph.drivers.base import InstagramDriver
from heliograph.drivers.uia import UiaDriver
from heliograph.drivers.uia.actions import escape_keys
from heliograph.drivers.uia.core import _same_place
from heliograph.drivers.uia.writes import is_write_control
from heliograph.errors import ElementNotFoundError, RateLimitedError, UnsafeActionError
from tests.uia.fakes import IG, home_tree, snap


class _NoWorker:
    """Fails the test if the driver tries to touch UI Automation."""

    async def run(self, *a: Any, **k: Any) -> Any:
        raise AssertionError("UIA must not be touched")

    def shutdown(self) -> None:
        pass


@pytest.fixture
def driver() -> UiaDriver:
    d = UiaDriver()
    d._worker = _NoWorker()  # type: ignore[assignment]
    d._snap = snap(home_tree())
    return d


def _ref(d: UiaDriver, name: str) -> str:
    return d._snap.find(name, "button")[0].ref  # type: ignore[union-attr]


def test_satisfies_protocol() -> None:
    assert isinstance(UiaDriver(), InstagramDriver)
    assert UiaDriver.name == "uia"


@pytest.mark.parametrize("op", ["like", "unlike", "save", "unsave", "follow"])
async def test_writes_require_confirm(driver: UiaDriver, op: str) -> None:
    with pytest.raises(UnsafeActionError):
        await getattr(driver, op)(_ref(driver, "Like"))


async def test_comment_requires_confirm(driver: UiaDriver) -> None:
    with pytest.raises(UnsafeActionError):
        await driver.comment(_ref(driver, "Comment"), "hi")


async def test_write_checks_target_name(driver: UiaDriver) -> None:
    with pytest.raises(ElementNotFoundError):
        await driver.like(_ref(driver, "Save"), confirm=True)


async def test_like_already_liked_is_noop(driver: UiaDriver) -> None:
    result = await driver.like(_ref(driver, "Unlike"), confirm=True)
    assert result == {"ref": _ref(driver, "Unlike"), "changed": False, "state": "Unlike"}


async def test_writes_are_rate_limited(driver: UiaDriver) -> None:
    driver.write_limiter.try_acquire("another process")  # shared ratelimit.json slot
    with pytest.raises(RateLimitedError) as err:
        await driver.save(_ref(driver, "Save"), confirm=True)
    assert err.value.retry_after and err.value.retry_after > 0


async def test_generic_click_refuses_write_controls(driver: UiaDriver, recorded: Any) -> None:
    with pytest.raises(UnsafeActionError):
        await driver.click(_ref(driver, "Like"))
    events = recorded(name="uia.click")
    assert events and events[-1]["status"] == "error"


async def test_click_unknown_ref(driver: UiaDriver) -> None:
    with pytest.raises(ElementNotFoundError):
        await driver.click("e9999")


async def test_navigate_refuses_foreign_urls(driver: UiaDriver) -> None:
    with pytest.raises(UnsafeActionError):
        await driver.navigate("https://evil.example/")


async def test_not_ready_raises_driver_unavailable() -> None:
    from heliograph.errors import DriverUnavailableError

    with pytest.raises(DriverUnavailableError):
        await UiaDriver().screenshot(__import__("pathlib").Path("x.png"))


def test_is_write_control() -> None:
    s = snap(home_tree())
    assert is_write_control(s.find("Like", "button")[0])
    assert not is_write_control(s.find("Home", "hyperlink")[0])
    assert not is_write_control(s.find("More Options", "button")[0])


def test_escape_keys() -> None:
    assert escape_keys("a{b}c") == "a{{}b{}}c"


@pytest.mark.parametrize(
    ("url", "target", "same"),
    [
        (IG + "reels/abc/", IG + "reels/", True),
        (IG + "reels/", IG + "reels/", True),
        (IG + "explore/", IG, False),
        (IG, IG, True),
        (IG + "direct/inbox/?x=1", IG + "direct/inbox/", True),
        (None, IG, False),
    ],
)
def test_same_place(url: str | None, target: str, same: bool) -> None:
    assert _same_place(url, target) is same
