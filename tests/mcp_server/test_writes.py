"""Confirm gating: without confirm=true nothing is sent; live-app writes are refused."""

from __future__ import annotations

from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from heliograph.drivers.uia.tree import Node, Snapshot
from tests.mcp_server.conftest import REEL, Harness, json_of

CODE = REEL["code"]
DRY_RUNS: list[tuple[str, dict[str, Any]]] = [
    ("ig_like", {"media": CODE}),
    ("ig_unlike", {"media": f"https://www.instagram.com/reel/{CODE}/"}),
    ("ig_save", {"media": CODE, "collection": "Trading strats"}),
    ("ig_unsave", {"media": CODE}),
    ("ig_follow", {"username": "@someone"}),
    ("ig_unfollow", {"username": "someone"}),
    ("ig_comment", {"media": CODE, "text": "nice"}),
    ("ig_send_dm", {"text": "hi", "username": "someone"}),
]


@pytest.mark.parametrize(("name", "args"), DRY_RUNS)
async def test_writes_dry_run_without_confirm(harness: Harness, name: str,
                                              args: dict[str, Any]) -> None:
    out = json_of(await harness.server.call_tool(name, args))
    assert out["performed"] is False and out["dry_run"] is True
    assert "confirm=true" in out["next_step"] and out["would"]
    assert harness.api.calls == [] and harness.service_builds == []


async def test_confirmed_like_goes_through_write_client(harness: Harness) -> None:
    pk = "3700000000000000001"
    harness.api.routes[f"/api/v1/web/likes/{pk}/like/"] = {"status": "ok"}
    out = json_of(await harness.server.call_tool("ig_like", {"media": pk, "confirm": True}))
    assert out["performed"] is True and out["status"] == "ok"
    method, path, _, write = harness.api.calls[-1]
    assert (method, write) == ("POST", True) and path.endswith("/like/")


async def test_write_validation(harness: Harness) -> None:
    with pytest.raises(ToolError, match="exactly one"):
        await harness.server.call_tool("ig_send_dm", {"text": "hi"})
    with pytest.raises(ToolError, match="empty"):
        await harness.server.call_tool("ig_comment", {"media": CODE, "text": "  "})


def _node(ref: str, role: str, name: str) -> Node:
    return Node(ref=ref, role=role, name=name, value="", rect=(0, 0, 10, 10), depth=0,
                parent=None, visible=True, interactive=True)


class FakeUia:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.last_snapshot = Snapshot(url="https://www.instagram.com/", title="Instagram",
                                      nodes=[_node("e1", "button", "Like"),
                                             _node("e2", "hyperlink", "Reels")])

    async def click(self, ref: str | None = None, **kw: Any) -> dict[str, Any]:
        self.calls.append(("click", ref or kw.get("name")))
        return {"ref": ref, "method": "invoke"}

    async def like(self, ref: str, *, confirm: bool = False) -> dict[str, Any]:
        self.calls.append(("like", (ref, confirm)))
        return {"ref": ref, "changed": True}

    async def type_text(self, text: str, ref: str | None = None, **kw: Any) -> None:
        self.calls.append(("type", text))


async def test_app_click_refuses_write_controls_without_confirm(harness: Harness) -> None:
    harness.uia = FakeUia()
    out = json_of(await harness.server.call_tool("app_click", {"ref": "e1"}))
    assert out["dry_run"] is True
    out = json_of(await harness.server.call_tool("app_click", {"name": "Follow"}))
    assert out["dry_run"] is True
    assert harness.uia.calls == []
    out = json_of(await harness.server.call_tool("app_click", {"ref": "e2"}))
    assert out["performed"] is True and harness.uia.calls == [("click", "e2")]


async def test_app_click_confirmed_uses_driver_write_method(harness: Harness) -> None:
    harness.uia = FakeUia()
    out = json_of(await harness.server.call_tool("app_click", {"ref": "e1", "confirm": True}))
    assert out["action"] == "like" and harness.uia.calls == [("like", ("e1", True))]


async def test_app_type_submit_requires_confirm(harness: Harness) -> None:
    harness.uia = FakeUia()
    out = json_of(await harness.server.call_tool("app_type", {"text": "hi", "submit": True}))
    assert out["dry_run"] is True and harness.uia.calls == []
    out = json_of(await harness.server.call_tool("app_type", {"text": "cats"}))
    assert out["performed"] is True and harness.uia.calls == [("type", "cats")]


async def test_app_type_newline_counts_as_submit(harness: Harness) -> None:
    harness.uia = FakeUia()
    out = json_of(await harness.server.call_tool("app_type", {"text": "nice post\n"}))
    assert out["dry_run"] is True and harness.uia.calls == []


class _RefusingUia(FakeUia):
    async def type_text(self, text: str, ref: str | None = None, **kw: Any) -> None:
        from heliograph.errors import UnsafeActionError

        assert kw.get("confirm") is False
        raise UnsafeActionError("the focused control 'Like' changes the account")


async def test_app_type_driver_refusal_becomes_dry_run(harness: Harness) -> None:
    harness.uia = _RefusingUia()
    out = json_of(await harness.server.call_tool("app_type", {"text": "a b"}))
    assert out["dry_run"] is True and "Like" in out["reason"]


async def test_app_click_checks_parent_write_control(harness: Harness) -> None:
    """An unnamed icon inside the Like button is a write control via the snapshot."""
    harness.uia = FakeUia()
    like = _node("e1", "button", "Like")
    icon = Node(ref="e2", role="image", name="", value="", rect=(0, 0, 5, 5), depth=1,
                parent="e1", visible=True, interactive=False)
    harness.uia.last_snapshot = Snapshot(url="https://www.instagram.com/", title="Instagram",
                                         nodes=[like, icon])
    out = json_of(await harness.server.call_tool("app_click", {"ref": "e2"}))
    assert out["dry_run"] is True and harness.uia.calls == []
