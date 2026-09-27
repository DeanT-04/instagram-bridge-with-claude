"""Snapshot compaction, refs, filters and text rendering over fake trees."""

from __future__ import annotations

import json

from heliograph.drivers.uia.tree import RawNode, build_snapshot
from tests.uia.fakes import IG, VIEW, btn, group, home_tree, link, n, snap, text


def test_compaction_drops_unnamed_structure_and_repeated_labels() -> None:
    root = n(
        "document",
        "T",
        group(group(btn("Save", btn("Save", n("image", "Save"))))),
        value=IG,
        rect=VIEW,
    )
    s = build_snapshot(root)
    assert [(x.role, x.name, x.depth) for x in s.nodes] == [("button", "Save", 0)]
    assert s.raw_count == 6 and s.url == IG and s.title == "T"


def test_refs_are_sequential_preorder_with_parents() -> None:
    s = snap(home_tree())
    assert [x.ref for x in s.nodes] == [f"e{i}" for i in range(1, len(s) + 1)]
    home = s.find("Home", "hyperlink")[0]
    assert s.get(home.ref) is home and home.parent is None
    article = s.find(role="article")[0]
    kids = s.subtree(article.ref)
    assert kids[0] is article and all(k.depth > article.depth for k in kids[1:])
    assert any(k.name == "Like" and k.parent == article.ref for k in kids)


def test_interactive_and_visible_filters_and_max_nodes() -> None:
    s = snap(home_tree())
    inter, _ = s.select(interactive_only=True)
    assert inter and all(x.interactive for x in inter)
    vis, _ = s.select(visible_only=True)
    assert all(x.visible for x in vis) and len(vis) < len(s)
    few, truncated = s.select(max_nodes=3)
    assert len(few) == 3 and truncated


def test_visibility_uses_offscreen_flag_and_viewport() -> None:
    root = n(
        "document",
        "T",
        btn("in"),
        btn("off", off=True),
        n("button", "outside", rect=(2000, 10, 2050, 50)),
        n("button", "empty", rect=(0, 0, 0, 0)),
        value=IG,
        rect=VIEW,
    )
    s = build_snapshot(root)
    assert {x.name: x.visible for x in s.nodes} == {
        "in": True,
        "off": False,
        "outside": False,
        "empty": False,
    }


def test_to_dict_is_json_serialisable() -> None:
    d = snap(home_tree()).to_dict(interactive_only=True, max_nodes=10)
    assert d["driver"] == "uia" and d["url"] == IG and d["truncated"] is True
    assert len(d["nodes"]) == 10 and {"ref", "role", "rect"} <= set(d["nodes"][0])
    json.dumps(d)


def test_render_text_outline() -> None:
    root = n(
        "document",
        "Title",
        link("Home", IG),
        group(btn("Like"), text("x" * 200)),
        btn("Hidden", off=True),
        value=IG,
        rect=VIEW,
    )
    s = build_snapshot(root)
    out = s.render_text(visible_only=False, max_name=20)
    lines = out.splitlines()
    assert lines[0] == f"# Title — {IG}"
    assert f'- hyperlink "Home" [e1] -> {IG}' in lines
    assert '- button "Like" [e2]' in lines
    assert any(line.endswith("… [e3]") or '…" [e3]' in line for line in lines)
    assert '- button "Hidden" [e4] (offscreen)' in lines
    assert "Hidden" not in s.render_text()  # visible_only by default


def test_find_substring_and_within() -> None:
    s = snap(home_tree())
    assert s.find("messages direct", exact=False)
    article = s.find(role="article")[0]
    likes = s.find("Like", "button", within=article.ref)
    assert len(likes) == 1


def test_handles_are_kept_per_ref() -> None:
    marker = object()
    root = RawNode(
        role="document",
        rect=VIEW,
        children=[RawNode("button", "Go", handle=marker, rect=(1, 1, 5, 5))],
    )
    s = build_snapshot(root)
    assert s.handle("e1") is marker and s.handle("e99") is None
