"""MCP tool registration, read tools, error mapping and tracing (all against fakes)."""

from __future__ import annotations

from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from heliograph.errors import NotLoggedInError, RateLimitedError
from heliograph.mcp import Runtime, build_server
from heliograph.mcp.common import error_text
from tests.mcp_server.conftest import REEL, FakeCdp, Harness, json_of

EXPECTED = {
    "heliograph_status", "heliograph_setup_check",
    "ig_whoami", "ig_get_user", "ig_user_posts", "ig_get_media", "ig_comments", "ig_search",
    "ig_timeline", "ig_reels_feed", "ig_explore", "ig_inbox", "ig_thread", "ig_activity",
    "ig_list_collections", "ig_collection_posts", "ig_saved_posts",
    "ig_extract_media", "ig_extract_collection", "ig_view_frames", "ig_read_dossier",
    "app_open", "app_snapshot", "app_screenshot", "app_navigate", "app_click", "app_scroll",
    "app_type", "app_visible_posts", "app_badges",
    "ig_like", "ig_unlike", "ig_save", "ig_unsave", "ig_follow", "ig_unfollow", "ig_comment",
    "ig_send_dm",
    "eye_report", "eye_trace", "eye_recent",
}
WRITES = {"ig_like", "ig_unlike", "ig_save", "ig_unsave", "ig_follow", "ig_unfollow",
          "ig_comment", "ig_send_dm", "app_click", "app_type"}


async def test_all_tools_registered_with_descriptions(harness: Harness) -> None:
    tools = {t.name: t for t in await harness.server.list_tools()}
    assert set(tools) == EXPECTED
    for t in tools.values():
        assert t.description and len(t.description) > 40, t.name
    for name in WRITES:
        props = tools[name].inputSchema["properties"]
        assert props["confirm"]["default"] is False, name
        assert tools[name].annotations and tools[name].annotations.readOnlyHint is False


async def test_server_name_and_instructions(harness: Harness) -> None:
    assert harness.server.name == "heliograph"
    assert "confirm=true" in (harness.server.instructions or "")


async def test_whoami_and_lazy_service(harness: Harness) -> None:
    assert harness.service_builds == []  # nothing built at startup
    assert json_of(await harness.server.call_tool("ig_whoami", {})) == {"username": "me",
                                                                         "pk": "1"}
    await harness.server.call_tool("ig_whoami", {})
    assert len(harness.service_builds) == 1


async def test_collection_posts_by_name(harness: Harness) -> None:
    out = json_of(await harness.server.call_tool(
        "ig_collection_posts", {"collection": "trading strats", "limit": 1, "caption_chars": 10}))
    assert out["collection_id"] == "7" and out["count"] == 1
    assert out["skipped_on_last_page"] == 1
    item = out["items"][0]
    assert item["code"] == REEL["code"] and len(item["caption"]) <= 10
    assert "raw" not in item and "images" not in item


async def test_saved_posts_follows_cursor_until_limit(harness: Harness) -> None:
    pages = {None: {"items": [{"media": REEL}] * 2, "more_available": True, "next_max_id": "p2"},
             "p2": {"items": [{"media": REEL}] * 2, "more_available": False}}
    harness.api.routes["/api/v1/feed/saved/posts/"] = lambda p: pages[p.get("max_id")]
    out = json_of(await harness.server.call_tool("ig_saved_posts", {"limit": 3}))
    assert out["count"] == 3 and out["has_more"] is False
    assert [c[2]["max_id"] for c in harness.api.calls] == [None, "p2"]


async def test_list_collections(harness: Harness) -> None:
    out = json_of(await harness.server.call_tool("ig_list_collections", {}))
    assert out == {"count": 1, "collections": [{"id": "7", "name": "Trading strats",
                                                "media_count": 2}]}


async def test_heliograph_error_maps_to_tool_error_with_hint_and_trace(recorded: Any) -> None:
    async def not_logged_in(rt: Runtime) -> Any:
        raise NotLoggedInError("The Heliograph browser profile is not logged in to Instagram")

    server = build_server(Runtime(cdp_factory=FakeCdp, service_factory=not_logged_in))
    with pytest.raises(ToolError) as info:
        await server.call_tool("ig_whoami", {})
    msg = str(info.value)
    assert "NotLoggedInError" in msg and "heliograph login" in msg and "Trace:" in msg
    spans = recorded(name="mcp.ig_whoami")
    assert spans and spans[-1]["status"] == "error"
    assert spans[-1]["trace_id"] in msg


async def test_invalid_argument_is_reported(harness: Harness) -> None:
    with pytest.raises(ToolError, match="limit must be at least 1"):
        await harness.server.call_tool("ig_saved_posts", {"limit": 0})


def test_error_text_variants() -> None:
    text = error_text(RateLimitedError("slow down", retry_after=12), "abc")
    assert "retry after ~12s" in text and "abc" in text
    assert "sessionid=[redacted]" in error_text(RuntimeError("cookie sessionid=SECRET1"), None)


async def test_each_call_is_a_traced_span(harness: Harness, recorded: Any) -> None:
    await harness.server.call_tool("ig_whoami", {})
    spans = recorded(name="mcp.ig_whoami")
    inner = recorded(name="ig.viewer")
    assert spans and inner and inner[-1]["trace_id"] == spans[-1]["trace_id"]


async def test_eye_tools(harness: Harness) -> None:
    await harness.server.call_tool("ig_whoami", {})
    recent = json_of(await harness.server.call_tool("eye_recent", {"name": "ig_whoami"}))
    assert recent["count"] >= 1
    trace_id = recent["events"][0]["trace_id"]
    trace = json_of(await harness.server.call_tool("eye_trace", {"trace_id": trace_id}))
    assert {e["name"] for e in trace["events"]} >= {"mcp.ig_whoami", "ig.viewer"}
    report = json_of(await harness.server.call_tool("eye_report", {"hours": 1}))
    assert report["total"] >= 2


async def test_uia_tools_fail_gracefully_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("heliograph.mcp.runtime.sys.platform", "linux")
    server = build_server(Runtime(cdp_factory=FakeCdp))
    with pytest.raises(ToolError, match="only work on Windows"):
        await server.call_tool("app_snapshot", {})


async def test_runtime_aclose_detaches(harness: Harness) -> None:
    drv = harness.runtime.cdp_driver()
    await harness.runtime.aclose()
    assert isinstance(drv, FakeCdp) and drv.closed and not await harness.runtime.cdp_connected()
