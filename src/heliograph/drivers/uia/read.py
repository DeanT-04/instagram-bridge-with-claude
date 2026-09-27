"""Higher-level readers over a :class:`~.tree.Snapshot` (pure functions, no UIA calls).

Post detection (live-verified structures, Instagram web in the Store app, Sept 2026):

* **Home feed / single post** — each post is an ``article`` landmark containing the author
  link, the permalink (``/p/<code>/`` time link), media (``Video player`` link or an image
  whose alt text starts "Photo by"), and the action row ``Like`` [count] ``Comment``
  [count] ``Repost`` ``Share`` ``Save``.
* **Reels** — no articles; each reel is a ``Video player`` group (author link
  ``"<user> reels"``, Follow button, caption as a leaf button) followed by its action
  column ``Like`` [count] ``Comment N`` ``Repost`` ``Share`` ``Save`` ``More``.

Toggled states: ``Unlike`` = already liked, ``Remove`` = already saved.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from heliograph.drivers.uia.tree import Node, Snapshot

__all__ = ["current_section", "parse_count", "unread_badges", "username_from_url", "visible_posts"]

_IG = re.compile(r"^https://(?:www\.)?instagram\.com/")
_RESERVED = {
    "explore",
    "reels",
    "reel",
    "direct",
    "accounts",
    "p",
    "stories",
    "tv",
    "about",
    "legal",
    "developer",
    "web",
    "emails",
    "challenge",
    "_n",
    "_u",
}
_PERMALINK = re.compile(r"instagram\.com/(p|reel|reels|tv)/([A-Za-z0-9_-]+)/?")
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


def username_from_url(url: str | None) -> str | None:
    """``https://www.instagram.com/alice/`` or ``.../alice/reels/`` -> ``"alice"``."""
    if not url or not _IG.match(url):
        return None
    parts = [p for p in urlparse(url).path.split("/") if p]
    if not parts or parts[0] in _RESERVED or not re.fullmatch(r"[A-Za-z0-9._]+", parts[0]):
        return None
    if len(parts) > 1 and parts[1] not in ("reels", "tagged", "saved", "followers", "following"):
        return None
    return parts[0]


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
        out.append(snap.nodes[begin:end])
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
        match = _PERMALINK.search(node.value)
        if permalink is None and match and "/audio/" not in node.value:
            permalink = f"https://www.instagram.com/{match.group(1)}/{match.group(2)}/"
            refs["permalink"] = node.ref
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
        "visible": any(n.visible for n in seg if n.role in ("button", "hyperlink")),
        "refs": refs,
    }


def visible_posts(snap: Snapshot, *, visible_only: bool = True) -> list[dict[str, Any]]:
    """Posts/reels in the snapshot as dicts (see module docstring for the fields).

    ``refs`` maps ``like``/``comment``/``save``/``more``/``author``/``permalink`` to
    snapshot refs usable with the driver's click/write methods. ``visible`` means some control
    of the post is on screen. When exactly one post's Like button is on screen on a
    ``/reels/<code>/`` page, that post's permalink is taken from the URL.
    """
    posts = [_parse_post(seg) for seg in _segments(snap)]
    posts = [p for p in posts if p["refs"].get("like") or p["author"]]
    if visible_only:
        posts = [p for p in posts if p["visible"]]
    match = _PERMALINK.search(snap.url or "")

    def like_visible(post: dict[str, Any]) -> bool:
        node = snap.get(post["refs"].get("like", ""))
        return bool(node and node.visible)

    focused = [p for p in posts if like_visible(p)]
    if match and len(focused) == 1 and focused[0]["permalink"] is None:
        focused[0]["permalink"] = f"https://www.instagram.com/{match.group(1)}/{match.group(2)}/"
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
    if not _IG.match(url):
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
