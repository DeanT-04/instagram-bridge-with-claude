"""A fake web-API client for service tests: canned responses by path, records every call."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

Response = dict[str, Any] | Callable[[dict[str, Any]], dict[str, Any]]


class FakeApi:
    """Returns ``routes[path]`` (a dict, or a function of the params/data) for each call."""

    def __init__(self, routes: dict[str, Response] | None = None) -> None:
        self.routes = routes or {}
        self.calls: list[tuple[str, str, dict[str, Any], bool]] = []

    def _answer(self, path: str, args: dict[str, Any]) -> dict[str, Any]:
        if path not in self.routes:
            raise AssertionError(f"unexpected call to {path}")
        resp = self.routes[path]
        return resp(args) if callable(resp) else resp

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append(("GET", path, dict(params or {}), False))
        return self._answer(path, dict(params or {}))

    async def post(
        self, path: str, data: dict[str, Any] | None = None, *,
        params: dict[str, Any] | None = None, write: bool = True,
    ) -> dict[str, Any]:
        self.calls.append(("POST", path, dict(data or {}), write))
        return self._answer(path, dict(data or {}))
