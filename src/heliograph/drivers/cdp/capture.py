"""Record Instagram JSON API/GraphQL responses observed while an action runs.

Usage::

    async with NetworkCapture(page) as cap:
        await page.goto(url)
    for r in cap.responses: ...
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, Protocol
from urllib.parse import parse_qs, urlparse

from heliograph.eye import event

__all__ = ["CapturedResponse", "NetworkCapture"]

DEFAULT_PATTERNS = ("/api/v1/", "/graphql", "/api/graphql")


class _Request(Protocol):
    method: str
    post_data: str | None


class _Response(Protocol):
    url: str
    status: int
    request: Any
    headers: dict[str, str]

    async def json(self) -> Any: ...


@dataclass
class CapturedResponse:
    """One recorded JSON response."""

    url: str
    status: int
    method: str
    data: Any
    friendly_name: str | None = None
    """GraphQL ``fb_api_req_friendly_name`` (from the request form body), if any."""

    @property
    def path(self) -> str:
        """URL path component."""
        return urlparse(self.url).path


@dataclass
class NetworkCapture:
    """Async context manager capturing JSON responses whose path matches ``patterns``.

    Only ``instagram.com`` hosts are recorded. ``max_responses`` bounds memory use.
    """

    page: Any
    """A Playwright ``Page`` (anything with ``on``/``remove_listener``)."""
    patterns: tuple[str, ...] = DEFAULT_PATTERNS
    max_responses: int = 200
    responses: list[CapturedResponse] = field(default_factory=list)
    _tasks: set[asyncio.Task[None]] = field(default_factory=set)

    def matches(self, url: str) -> bool:
        """True if ``url`` is an Instagram API/GraphQL URL we want to keep."""
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if not (host == "instagram.com" or host.endswith(".instagram.com")):
            return False
        return any(p in parsed.path for p in self.patterns)

    def _on_response(self, response: _Response) -> None:
        if len(self.responses) + len(self._tasks) >= self.max_responses:
            return
        if not self.matches(response.url):
            return
        ctype = (response.headers or {}).get("content-type", "")
        if "json" not in ctype and "javascript" not in ctype and "text" not in ctype:
            return
        task = asyncio.ensure_future(self._read(response))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _read(self, response: _Response) -> None:
        try:
            data = await response.json()
        except Exception:  # body evicted, not JSON, navigation raced...
            return
        req = response.request
        post = getattr(req, "post_data", None) or ""
        friendly = parse_qs(post).get("fb_api_req_friendly_name", [None])[0] if post else None
        self.responses.append(CapturedResponse(
            url=response.url, status=response.status,
            method=str(getattr(req, "method", "GET")), data=data, friendly_name=friendly,
        ))

    async def __aenter__(self) -> NetworkCapture:
        self.page.on("response", self._on_response)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.page.remove_listener("response", self._on_response)
        if self._tasks:
            await asyncio.wait(set(self._tasks), timeout=10)
        event("cdp.capture", count=len(self.responses),
              paths=sorted({r.path for r in self.responses})[:30])
