"""NetworkCapture and CdpDriver pieces that don't need a browser."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest

from heliograph.drivers.cdp.capture import NetworkCapture
from heliograph.drivers.cdp.session import CdpDriver, is_instagram_url
from heliograph.errors import DriverUnavailableError


@dataclass
class FakeRequest:
    method: str = "GET"
    post_data: str | None = None


@dataclass
class FakeResponse:
    url: str
    data: Any
    status: int = 200
    headers: dict[str, str] = field(default_factory=lambda: {"content-type": "application/json"})
    request: FakeRequest = field(default_factory=FakeRequest)

    async def json(self) -> Any:
        return self.data


class FakeEvents:
    def __init__(self) -> None:
        self.handlers: dict[str, list[Callable[..., Any]]] = {}

    def on(self, event: str, f: Callable[..., Any]) -> None:
        self.handlers.setdefault(event, []).append(f)

    def remove_listener(self, event: str, f: Callable[..., Any]) -> None:
        self.handlers[event].remove(f)

    def emit(self, event: str, arg: Any) -> None:
        for f in list(self.handlers.get(event, [])):
            f(arg)


async def test_capture_filters_and_reads_json() -> None:
    page = FakeEvents()
    async with NetworkCapture(page) as cap:
        page.emit("response", FakeResponse("https://www.instagram.com/api/v1/feed/saved/posts/",
                                           {"items": [1]}))
        page.emit("response", FakeResponse(
            "https://www.instagram.com/graphql/query", {"data": {}},
            request=FakeRequest("POST", "fb_api_req_friendly_name=PolarisSavedQuery&doc_id=1")))
        page.emit("response", FakeResponse("https://evil.example/api/v1/x", {"no": 1}))
        page.emit("response", FakeResponse("https://www.instagram.com/static/app.js", {"no": 1}))
        page.emit("response", FakeResponse("https://www.instagram.com/api/v1/img", b"",
                                           headers={"content-type": "image/png"}))
    assert [r.path for r in cap.responses] == ["/api/v1/feed/saved/posts/", "/graphql/query"]
    assert cap.responses[1].friendly_name == "PolarisSavedQuery"
    assert page.handlers["response"] == []


@pytest.mark.parametrize(
    ("url", "ok"),
    [("https://www.instagram.com/p/x/", True), ("https://instagram.com/", True),
     ("http://www.instagram.com/", False), ("https://www.instagram.com.evil.io/", False),
     ("javascript:alert(1)", False)],
)
def test_is_instagram_url(url: str, ok: bool) -> None:
    assert is_instagram_url(url) is ok


async def test_driver_requires_connection_and_rejects_foreign_urls() -> None:
    driver = CdpDriver(launch=False)
    assert driver.name == "cdp"
    assert await driver.current_url() is None
    with pytest.raises(DriverUnavailableError):
        _ = driver.page
    with pytest.raises(ValueError):
        await driver.navigate("https://example.com/")
