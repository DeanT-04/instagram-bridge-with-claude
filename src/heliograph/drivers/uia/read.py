"""Higher-level readers over a :class:`~.tree.Snapshot` (pure functions, no UIA calls).

Post detection (live-verified structures, Instagram web in the Store app, Sept 2026):

* **Home feed / single post** — each post is an ``article`` landmark containing the author
  link, the permalink (``/p/<code>/`` time link), media (``Video player`` link or an image
  whose alt text starts "Photo by"), and the action row ``Like`` [count] ``Comment``
  [count] ``Repost`` ``Share`` ``Save``.
* **Reels** — no articles; each reel is a ``Video player`` group (author link
  ``"<user> reels"``, Follow button, caption as a leaf button) followed by its action
  column ``Like`` [count] ``Comment N`` ``Repost`` ``Share`` ``Save`` ``More``.
* **Grids** (profile posts/reels tabs, saved, explore, "more posts" under a post) — no
  articles or action rows; each tile is a hyperlink to ``/<owner>/p/<code>/`` or
  ``/<owner>/reel/<code>/`` (owner prefix on profile grids) named ``"<alt text> Clip"`` /
  ``"... Carousel"`` (or unnamed). These come back with ``kind="grid"`` and null counts.

Lazy loading: right after launch/navigation the document can be nearly empty (a handful of
nodes) or show ``statusbar "Loading..."`` placeholders; :func:`is_loading` detects that so
the driver can wait instead of reporting zero posts.

Toggled states: ``Unlike`` = already liked, ``Remove`` = already saved.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from heliograph.drivers.uia.grid import grid_posts
from heliograph.drivers.uia.tree import Node, Snapshot
from heliograph.drivers.uia.urls import IG_URL, permalink_of, username_from_url

__all__ = [
    "POST_SECTIONS",
    "current_section",
    "is_loading",
    "parse_count",
    "permalink_of",
    "unread_badges",
    "username_from_url",
    "visible_posts",
]

POST_SECTIONS = frozenset(
    {"home", "reels", "reel", "profile", "own_profile", "saved", "post", "explore"}
)
"""Sections that normally show posts (worth waiting for when none are found yet)."""
_COMMENT = re.compile(r"Comment( [\d,.]+[KkMm]?)?")
_COUNT = re.compile(r"^(\d[\d,.]*)\s*([KkMm])?$")
_NOT_CAPTION = {
    "more",
    "follow",
    "following",
    "more options",
    "share",
    "repost",
    "like",
    "unlike",
    "save",
    "remove",
    "comment",
    "press to play",
    "video player",
    "verified",
    "audio image",
    "adjust volume",
    "•",
    "...",
    "ad",
    "sponsored",
    "link in bio!",
    "next",
    "previous",
    "close",
    "tagged",
    "play",
    "pause",
}
_LIKE = {"Like", "Unlike"}
_SAVE = {"Save", "Remove"}


def parse_count(text: str) -> int | None:
    """``"7,458"`` -> 7458, ``"1.2K"`` -> 1200, ``"3M"`` -> 3000000; else None."""
    match = _COUNT.match(text.strip())
    if not match:
        return None
    number, suffix = match.groups()
    try:
        if suffix:
            value = float(number.replace(",", "")) * (1000 if suffix in "Kk" else 1_000_000)
            return round(value)
        return int(number.replace(",", "").replace(".", ""))
    except ValueError:
        return None


def is_loading(snap: Snapshot) -> bool:
    """True while the page is still (lazily) rendering: an almost empty document or
    ``Loading...`` status/progress placeholders."""
    if len(snap.nodes) <= 15:
        return True
    return any(
        n.role in ("statusbar", "progressbar") and n.name.casefold().startswith("loading")
        for n in snap.nodes
    )


def _segments(snap: Snapshot) -> list[list[Node]]:
    articles = snap.find(role="article")
    if articles:
        return [snap.subtree(a.ref) for a in articles]
    starts = [
        n for n in snap.nodes if n.name == "Video player" and n.role in ("group", "hyperlink")
    ]
    out: list[list[Node]] = []
    for i, start in enumerate(starts):
        begin = snap.index_of(start.ref)
        end = snap.index_of(starts[i + 1].ref) if i + 1 < len(starts) else len(snap.nodes)
        seg = snap.nodes[begin:end]
        # stop at the reel container's end (the last reel must not swallow the page's
        # trailing "Navigate to previous reel" / Messages buttons)
        cut = next((j for j, node in enumerate(seg) if node.depth < start.depth), len(seg))
        out.append(seg[:cut])
    return out


def _next_count(seg: list[Node], ref: str | None) -> int | None:
    if ref is None:
        return None
    idx = next(i for i, n in enumerate(seg) if n.ref == ref)
    for node in seg[idx + 1 : idx + 3]:
        if node.role == "button":
            return parse_count(node.name)
    return None


def _caption(seg: list[Node], author: str | None) -> str | None:
    parents = {n.parent for n in seg if n.parent}
    link_refs = {n.ref for n in seg if n.role == "hyperlink"}
    best = ""
    for node in seg:
        if node.role not in ("text", "button") or node.ref in parents or node.parent in link_refs:
            continue
        name = node.name.strip()
        low = name.casefold()
        if (
            len(name) < 2
            or low in _NOT_CAPTION
            or low.startswith("comment")
            or parse_count(name) is not None
            or name == author
            or name.endswith("profile picture")
            or name.startswith("Story by")
        ):
            continue
        if len(name) > len(best):
            best = name
    return best or None


def _parse_post(seg: list[Node]) -> dict[str, Any]:
    author = permalink = None
    refs: dict[str, str] = {}
    for node in seg:
        if node.role != "hyperlink":
            continue
        user = username_from_url(node.value)
        if author is None and user and node.name.startswith(user):
            author, refs["author"] = user, node.ref
        found = permalink_of(node.value)
        if permalink is None and found:
            permalink, refs["permalink"] = found[0], node.ref
    like = next((n for n in seg if n.role == "button" and n.name in _LIKE), None)
    save = next((n for n in seg if n.role == "button" and n.name in _SAVE), None)
    comment = next((n for n in seg if n.role == "button" and _COMMENT.fullmatch(n.name)), None)
    more = next((n for n in seg if n.role == "button" and n.name in ("More", "More Options")), None)
    for key, control in (("like", like), ("comment", comment), ("save", save), ("more", more)):
        if control is not None:
            refs[key] = control.ref
    comment_count = None
    if comment is not None:
        tail = comment.name.removeprefix("Comment").strip()
        comment_count = parse_count(tail) if tail else _next_count(seg, comment.ref)
    alt = next((n.name for n in seg if n.role == "image" and n.name.startswith("Photo")), None)
    return {
        "kind": "feed" if seg[0].role == "article" else "reel",
        "author": author,
        "caption": _caption(seg, author),
        "permalink": permalink,
        "like_count": _next_count(seg, like.ref if like else None),
        "comment_count": comment_count,
        "liked": like.name == "Unlike" if like else None,
        "saved": save.name == "Remove" if save else None,
        "has_video": any(n.name in ("Video player", "Press to play", "Adjust volume") for n in seg),
        "is_ad": any(n.role == "text" and n.name in ("Ad", "Sponsored") for n in seg),
        "media_alt": alt,
        # the article/player itself counts: a tall photo can fill the screen while the
        # header and action row are both scrolled out of view
        "visible": seg[0].visible
        or any(n.visible for n in seg if n.role in ("button", "hyperlink")),
        "refs": refs,
    }


def visible_posts(snap: Snapshot, *, visible_only: bool = True) -> list[dict[str, Any]]:
    """Posts/reels in the snapshot as dicts (see module docstring for the fields).

    ``kind`` is ``feed`` (article), ``reel`` (Reels viewer) or ``grid`` (profile/saved/
    explore tile: only author, caption/alt, permalink and ``refs.permalink``).
    ``refs`` maps ``like``/``comment``/``save``/``more``/``author``/``permalink`` to
    snapshot refs usable with the driver's click/write methods. ``visible`` means some control
    of the post is on screen. When exactly one post's Like button is on screen on a
    ``/reels/<code>/`` page, that post's permalink is taken from the URL.
    """
    segments = _segments(snap)
    posts = [_parse_post(seg) for seg in segments]
    posts = [p for p in posts if p["refs"].get("like") or p["author"]]
    posts += grid_posts(snap, {n.ref for seg in segments for n in seg})
    if visible_only:
        posts = [p for p in posts if p["visible"]]
    here = permalink_of(snap.url)

    def like_visible(post: dict[str, Any]) -> bool:
        node = snap.get(post["refs"].get("like", ""))
        return bool(node and node.visible)

    focused = [p for p in posts if like_visible(p)]
    if here and len(focused) == 1 and focused[0]["permalink"] is None:
        focused[0]["permalink"] = here[0]
    return posts


def unread_badges(snap: Snapshot) -> dict[str, int]:
    """Unread counts from the nav, e.g. ``{"messages": 1, "notifications": 0}``."""
    out = {"messages": 0, "notifications": 0}
    for node in snap.nodes:
        if node.role not in ("hyperlink", "button"):
            continue
        found = re.search(r"(\d+) new notification", node.name)
        if not found:
            continue
        key = (
            "messages"
            if node.name.startswith(("Messages", "Direct")) or "/direct/" in node.value
            else "notifications"
            if node.name.startswith("Notifications")
            else None
        )
        if key:
            out[key] = max(out[key], int(found.group(1)))
    return out


def current_section(snap: Snapshot, own_username: str | None = None) -> str:
    """Best guess of where the app is: home, reels, reel, explore, search, messages,
    notifications, profile, own_profile, saved, post, stories, login or unknown."""
    if snap.find("Notifications", role="text") and snap.find("Close", role="button"):
        return "notifications"  # the notifications panel overlays the current page
    if snap.find("Search input", role="edit"):
        return "search"
    url = snap.url or ""
    if not IG_URL.match(url):
        return "unknown"
    parts = [p for p in urlparse(url).path.split("/") if p]
    if not parts:
        return "home"
    head = parts[0]
    simple = {"explore": "explore", "direct": "messages", "stories": "stories", "p": "post"}
    if head in simple:
        return simple[head]
    if head in ("reels", "reel"):
        return "reel" if len(parts) > 1 and parts[1] != "audio" else "reels"
    if head == "accounts":
        if "login" in parts:
            return "login"
        return "notifications" if "activity" in parts else "settings"
    if len(parts) > 1 and parts[1] == "saved":
        return "saved"
    if username_from_url(url):
        return "own_profile" if own_username and head == own_username else "profile"
    return "unknown"
