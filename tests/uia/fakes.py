"""Fake UIA trees shaped like the live Instagram Store app (captured Sept 2026)."""

from __future__ import annotations

from heliograph.drivers.uia.tree import RawNode, Snapshot, build_snapshot

IG = "https://www.instagram.com/"
VIEW = (0, 0, 1500, 1000)
ON = (10, 10, 50, 50)
OFF = (10, 2000, 50, 2040)


def n(role: str, name: str = "", *kids: RawNode, value: str = "", rect=ON, off=False) -> RawNode:
    """Build a RawNode (``off`` marks it offscreen)."""
    return RawNode(role=role, name=name, value=value, rect=rect, offscreen=off, children=list(kids))


def btn(name: str, *kids: RawNode, off: bool = False) -> RawNode:
    return n("button", name, *kids, rect=OFF if off else ON, off=off)


def link(name: str, url: str, *kids: RawNode, off: bool = False, rect=None) -> RawNode:
    return n("hyperlink", name, *kids, value=url, rect=rect or (OFF if off else ON), off=off)


def text(name: str) -> RawNode:
    return n("text", name)


def group(*kids: RawNode, name: str = "") -> RawNode:
    return n("group", name, *kids)


def nav_rail(user: str = "me_user", messages: int = 1) -> list[RawNode]:
    col = (59, 0, 131, 80)
    msg = (
        f"Messages Direct messaging – {messages} new notification link" if messages else "Messages"
    )
    return [
        link("Instagram", IG, rect=col),
        link("Home", IG, rect=col),
        link("Reels", IG + "reels/", rect=col),
        link(msg, IG + "direct/inbox/", n("image", "Messages"), rect=col),
        link("Search", IG + "explore/", rect=col),
        link("Notifications", IG + "#", rect=col),
        link("New post", IG + "#", rect=col),
        link(
            f"{user}'s profile picture",
            f"{IG}{user}/",
            link(f"{user}'s profile picture", ""),
            rect=col,
        ),
    ]


def feed_article(
    author: str,
    code: str,
    caption: str,
    likes: str,
    comments: str,
    *,
    liked: bool = False,
    off: bool = False,
) -> RawNode:
    return n(
        "group",
        "",
        btn(f"{author}'s profile picture", link(f"{author}'s profile picture", "")),
        link(author, f"{IG}{author}/", text(author)),
        text("•"),
        link("1 h", f"{IG}p/{code}/", group(text("1 h"))),
        btn("More Options", n("image", "More Options")),
        link("Video player", f"{IG}reels/{code}/", group(name="Video player")),
        btn("Unlike" if liked else "Like", n("image", "Like"), off=off),
        btn(likes, off=off),
        btn("Comment", n("image", "Comment"), off=off),
        btn(comments, off=off),
        btn("Repost"),
        btn("Share"),
        btn("Save", btn("Save", n("image", "Save")), off=off),
        link(f"{author} Verified", f"{IG}{author}/", text(author)),
        text(caption),
        btn("more"),
    )


def as_article(node: RawNode) -> RawNode:
    node.role = "article"
    return node


def offscreen(node: RawNode) -> RawNode:
    """Mark ``node`` and all descendants offscreen (below the fold)."""
    node.offscreen, node.rect = True, OFF
    for kid in node.children:
        offscreen(kid)
    return node


def home_tree(*, user: str = "me_user") -> RawNode:
    main = n(
        "main",
        "",
        n("list", "", n("listitem", "alice", btn("Story by alice, not seen"))),
        as_article(feed_article("tqe_trades", "Ddx4oj", "Leave EMA in the comments", "8", "21")),
        as_article(feed_article("fio.na", "DdoTPH", "sunset", "1,204", "3", liked=True, off=True)),
        offscreen(as_article(feed_article("zed", "DdZZZ", "far below", "1.2K", "0"))),
    )
    return n(
        "document",
        "(1) Instagram",
        group(*nav_rail(user)),
        main,
        btn("Messages - 1 new notification"),
        value=IG,
        rect=VIEW,
    )


def reel(author: str, caption: str, likes: str, comments: int, *, off: bool = False) -> list:
    player = group(
        btn(
            f"0 {author} reels {caption}",
            n("slider", "Adjust volume"),
            link(f"{author} reels", f"{IG}{author}/reels/", text(author)),
            btn("Follow"),
            btn(caption),
        ),
        name="Video player",
    )
    return [
        player,
        btn("Like", n("image", "Like"), off=off),
        btn(likes, off=off),
        btn(f"Comment {comments}", text(str(comments)), off=off),
        btn("Repost"),
        btn("Share"),
        btn("Save"),
        btn("More"),
        link("Audio image", f"{IG}reels/audio/123/"),
    ]


def reels_tree() -> RawNode:
    main = n(
        "main",
        "",
        group(
            *reel("creator.one", "A synthetic comedy clip", "2,869", 12),
            *reel("_creator_two", "a synthetic travel clip", "511", 11, off=True),
        ),
        btn("Navigate to next reel"),
    )
    return n(
        "document", "Instagram", group(*nav_rail()), main, value=IG + "reels/DSynth01/", rect=VIEW
    )


def snap(root: RawNode) -> Snapshot:
    return build_snapshot(root)
