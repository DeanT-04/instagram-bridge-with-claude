"""Readers (posts, badges, section) and nav resolution over fake trees."""

from __future__ import annotations

import pytest

from heliograph.drivers.uia import nav, read
from heliograph.drivers.uia.tree import build_snapshot
from tests.uia.fakes import IG, VIEW, btn, home_tree, link, n, reels_tree, snap, text


@pytest.mark.parametrize(
    ("raw", "value"),
    [("8", 8), ("7,458", 7458), ("1.2K", 1200), ("3M", 3_000_000), ("Like", None), ("", None)],
)
def test_parse_count(raw: str, value: int | None) -> None:
    assert read.parse_count(raw) == value


@pytest.mark.parametrize(
    ("url", "user"),
    [
        (IG + "alice/", "alice"),
        (IG + "al.ice_9/reels/", "al.ice_9"),
        (IG + "alice/saved/", "alice"),
        (IG + "explore/", None),
        (IG + "p/abc/", None),
        (IG + "alice/p/x/", None),
        ("https://example.com/alice/", None),
        (None, None),
    ],
)
def test_username_from_url(url: str | None, user: str | None) -> None:
    assert read.username_from_url(url) == user


def test_feed_posts() -> None:
    s = snap(home_tree())
    posts = read.visible_posts(s)
    assert [p["author"] for p in posts] == ["tqe_trades", "fio.na"]  # "zed" is offscreen
    first, second = posts
    assert first["caption"] == "Leave EMA in the comments"
    assert first["permalink"] == IG + "p/Ddx4oj/"
    assert (first["like_count"], first["comment_count"]) == (8, 21)
    assert first["has_video"] and first["liked"] is False and first["saved"] is False
    assert s.get(first["refs"]["like"]).name == "Like"  # type: ignore[union-attr]
    assert s.get(first["refs"]["save"]).name == "Save"  # type: ignore[union-attr]
    assert s.get(first["refs"]["comment"]).name == "Comment"  # type: ignore[union-attr]
    assert second["liked"] is True and second["like_count"] == 1204
    everything = read.visible_posts(s, visible_only=False)
    assert everything[-1]["author"] == "zed" and everything[-1]["like_count"] == 1200


def test_reel_posts_use_video_player_segments() -> None:
    s = snap(reels_tree())
    posts = read.visible_posts(s, visible_only=False)
    assert [p["author"] for p in posts] == ["creator.one", "_creator_two"]
    first = posts[0]
    assert first["caption"] == "A synthetic comedy clip"
    assert (first["like_count"], first["comment_count"]) == (2869, 12)
    assert first["permalink"] == IG + "reels/DSynth01/"  # from URL: only on-screen reel
    assert posts[1]["permalink"] is None and posts[1]["comment_count"] == 11


def test_unread_badges() -> None:
    assert read.unread_badges(snap(home_tree())) == {"messages": 1, "notifications": 0}
    root = n("document", "", link("Notifications 3 new notifications", IG + "#"), rect=VIEW)
    assert read.unread_badges(build_snapshot(root))["notifications"] == 3


@pytest.mark.parametrize(
    ("url", "section"),
    [
        (IG, "home"),
        (IG + "reels/", "reels"),
        (IG + "reels/abc/", "reel"),
        (IG + "explore/", "explore"),
        (IG + "direct/inbox/", "messages"),
        (IG + "me_user/", "own_profile"),
        (IG + "bob/", "profile"),
        (IG + "me_user/saved/", "saved"),
        (IG + "p/xyz/", "post"),
        (IG + "accounts/login/", "login"),
        ("about:blank", "unknown"),
    ],
)
def test_current_section_from_url(url: str, section: str) -> None:
    s = build_snapshot(n("document", "", btn("x"), value=url, rect=VIEW))
    assert read.current_section(s, "me_user") == section


def test_current_section_panels() -> None:
    search = build_snapshot(n("document", "", n("edit", "Search input"), value=IG, rect=VIEW))
    assert read.current_section(search) == "search"
    notif = build_snapshot(
        n("document", "", btn("Close"), text("Notifications"), value=IG, rect=VIEW)
    )
    assert read.current_section(notif) == "notifications"


def test_nav_resolution() -> None:
    s = snap(home_tree(user="me_user"))
    assert nav.own_username(s) == "me_user"
    assert nav.resolve_section(s, "reels").node.name == "Reels"  # type: ignore[union-attr]
    assert nav.resolve_section(s, "messages").node.value == IG + "direct/inbox/"  # type: ignore[union-attr]
    assert nav.resolve_section(s, "search").node.name == "Search"  # type: ignore[union-attr]
    assert nav.resolve_section(s, "create").node.name == "New post"  # type: ignore[union-attr]
    prof = nav.resolve_section(s, "profile").node
    assert prof is not None and prof.value == IG + "me_user/"
    assert nav.resolve_section(s, "saved").url == IG + "me_user/saved/"
    explore = nav.resolve_section(s, "explore")  # no "Explore" link in the narrow layout
    assert explore.node is None and explore.url == IG + "explore/"
    with pytest.raises(ValueError):
        nav.resolve_section(s, "nope")


def test_nav_ignores_links_when_rail_hidden() -> None:
    """With the notifications panel open the rail is gone; other users' links must not
    be mistaken for the profile link."""
    root = n(
        "document",
        "",
        btn("Close"),
        text("Notifications"),
        link("bob's profile picture", IG + "bob/"),
        value=IG,
        rect=VIEW,
    )
    s = build_snapshot(root)
    assert nav.own_username(s) is None
    target = nav.resolve_section(s, "profile")
    assert target.node is None and target.url is None
