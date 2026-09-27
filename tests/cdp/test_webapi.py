"""WebApiClient: URL building, request shape, error mapping (no network)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from heliograph.drivers.cdp.ratelimit import RateLimiter
from heliograph.drivers.cdp.webapi import InstagramApiError, WebApiClient, build_url
from heliograph.errors import NotLoggedInError, RateLimitedError
from tests.conftest import Recorded


class FakePage:
    """Stands in for a Playwright Page: records evaluate() args, returns a canned result."""

    def __init__(self, status: int = 200, body: Any = None, url: str = "", **extra: Any) -> None:
        self.calls: list[list[Any]] = []
        text = body if isinstance(body, str) else json.dumps(body if body is not None else {})
        self.result = {"status": status, "body": text, "url": url, "redirected": False, **extra}

    async def evaluate(self, expression: str, arg: Any = None) -> Any:
        self.calls.append(arg)
        return self.result


def _client(page: FakePage) -> WebApiClient:
    return WebApiClient(page, read_limiter=RateLimiter(0, 0), write_limiter=RateLimiter(0, 0))


def test_build_url_encodes_and_drops_none() -> None:
    url = build_url("/api/v1/x/", {"a": 1, "b": None, "c": True, "d": ["x"]})
    assert url == "/api/v1/x/?a=1&c=true&d=%5B%22x%22%5D"


@pytest.mark.parametrize("bad", ["https://evil.com/api/v1/", "/accounts/login/", "/api/v1//x",
                                 "//evil.com/api/v1/"])
def test_build_url_rejects_non_api_paths(bad: str) -> None:
    with pytest.raises(ValueError):
        build_url(bad)


async def test_get_returns_json_and_records_span(recorded: Recorded) -> None:
    page = FakePage(body={"status": "ok", "items": []})
    data = await _client(page).get("/api/v1/feed/saved/posts/", {"max_id": "abc"})
    assert data["status"] == "ok"
    path, method, body, app_id, _ = page.calls[0]
    assert (path, method, body, app_id) == (
        "/api/v1/feed/saved/posts/?max_id=abc", "GET", "", "936619743392459")
    spans = recorded(name="cdp.api")
    assert spans and spans[0]["attrs"]["status"] == 200


async def test_post_form_encodes_and_uses_write_limiter() -> None:
    page = FakePage(body={"status": "ok"})
    waits: list[str] = []

    class Spy(RateLimiter):
        async def acquire(self) -> float:
            waits.append(self.name)
            return 0.0

    client = WebApiClient(page, read_limiter=Spy(0, 0, name="r"), write_limiter=Spy(0, 0, name="w"))
    await client.post("/api/v1/web/comments/1/add/", {"comment_text": "hi there", "x": None})
    assert page.calls[0][1:3] == ["POST", "comment_text=hi+there"]
    await client.post("/api/v1/feed/timeline/", {}, write=False)
    assert waits == ["w", "r"]


@pytest.mark.parametrize(
    ("status", "body", "url", "exc"),
    [
        (401, "{}", "", NotLoggedInError),
        (200, "<html>", "https://www.instagram.com/accounts/login/?next=/", NotLoggedInError),
        (403, '{"message": "login_required", "status": "fail"}', "", NotLoggedInError),
        (429, "", "", RateLimitedError),
        (200, "<html>Please wait a few minutes before you try again.</html>", "", RateLimitedError),
        (400, '{"message": "feedback_required", "spam": true}', "", RateLimitedError),
        (404, '{"message": "", "status": "fail"}', "", InstagramApiError),
        (200, "<!DOCTYPE html><html></html>", "https://www.instagram.com/", InstagramApiError),
        (200, "[1, 2]", "", InstagramApiError),
    ],
)
async def test_error_mapping(status: int, body: str, url: str, exc: type[Exception]) -> None:
    with pytest.raises(exc):
        await _client(FakePage(status, body, url)).get("/api/v1/x/")


async def test_rate_limit_retry_after_header() -> None:
    with pytest.raises(RateLimitedError) as info:
        await _client(FakePage(429, "", retryAfter="120")).get("/api/v1/x/")
    assert info.value.retry_after == 120.0


async def test_404_carries_status() -> None:
    with pytest.raises(InstagramApiError) as info:
        await _client(FakePage(404, '{"status": "fail"}')).get("/api/v1/collections/list/")
    assert info.value.status == 404 and info.value.path == "/api/v1/collections/list/"
