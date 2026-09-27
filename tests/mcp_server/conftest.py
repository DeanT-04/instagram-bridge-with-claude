"""Fakes for MCP tool tests: a real InstagramService over a fake API, fake drivers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from mcp.server.fastmcp import FastMCP

from heliograph.instagram.models import Collection
from heliograph.instagram.service import InstagramService
from heliograph.mcp import Runtime, build_server
from tests.instagram.fakes import FakeApi

FIXTURES = json.loads((Path(__file__).parents[1] / "fixtures" / "media_items.json").read_text())
REEL: dict[str, Any] = FIXTURES["reel"]


class FakeLister:
    async def list(self, username: str) -> list[Collection]:
        return [Collection(id="7", name="Trading strats", media_count=2)]


class FakeLauncher:
    def find_running(self) -> None:
        return None


class FakeCdp:
    """Stands in for CdpDriver; never touches a browser."""

    def __init__(self, account: str) -> None:
        self.account = account
        self.launcher = FakeLauncher()
        self.closed = False

    async def connect(self) -> None:
        raise AssertionError("tests must not connect a browser")

    async def close(self) -> None:
        self.closed = True

    async def current_url(self) -> str | None:
        return None


def default_routes() -> dict[str, Any]:
    return {
        "/api/v1/accounts/edit/web_form_data/": {"form_data": {"username": "me"}},
        "/api/v1/feed/collection/7/posts/": {"items": [{"media": REEL}] * 2,
                                             "more_available": False},
        "/api/v1/feed/saved/posts/": {"items": [{"media": REEL}] * 3, "more_available": True,
                                      "next_max_id": "c2"},
    }


@dataclass
class Harness:
    server: FastMCP
    api: FakeApi
    runtime: Runtime
    uia: Any = None
    service_builds: list[int] = field(default_factory=list)


@pytest.fixture
def harness() -> Harness:
    api = FakeApi(default_routes())
    h = Harness(server=None, api=api, runtime=None)  # type: ignore[arg-type]

    async def service_factory(rt: Runtime) -> InstagramService:
        h.service_builds.append(1)
        return InstagramService(api, collections=FakeLister(), viewer_id="1")  # type: ignore[arg-type]

    async def uia_factory() -> Any:
        if h.uia is None:
            raise AssertionError("no fake UIA driver configured")
        return h.uia

    h.runtime = Runtime(cdp_factory=FakeCdp, service_factory=service_factory,
                        uia_factory=uia_factory)
    h.server = build_server(h.runtime)
    return h


def text_of(result: Any) -> str:
    """Joined text of a FastMCP call_tool result."""
    blocks = result[0] if isinstance(result, tuple) else result
    return "".join(getattr(b, "text", "") for b in blocks)


def json_of(result: Any) -> Any:
    return json.loads(text_of(result))
