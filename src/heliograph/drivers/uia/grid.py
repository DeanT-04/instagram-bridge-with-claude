"""Grid tiles (profile posts/reels tabs, saved, explore, "more posts") as post dicts (pure).

Each tile is a hyperlink to ``/<owner>/p/<code>/`` or ``/<owner>/reel/<code>/`` named
``"<alt text> Clip"`` / ``"... Carousel"`` (or unnamed), live-verified Sept 2026.
"""

from __future__ import annotations

import re
from typing import Any

from heliograph.drivers.uia.tree import Snapshot
from heliograph.drivers.uia.urls import permalink_of, username_from_url

__all__ = ["grid_posts"]

# the tile type icon label is appended to the alt text: "<alt> Clip", "<alt> Carousel"
_TILE_KIND = re.compile(r"(?:^|\s+)(Clip|Carousel)$")


def grid_posts(snap: Snapshot, covered: set[str]) -> list[dict[str, Any]]:
    """Post dicts (``kind="grid"``) for post/reel links not in ``covered`` (refs already
    parsed as feed/reel posts), one per permalink; owner from the link or the page URL."""
    page_owner = username_from_url(snap.url)
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for node in snap.nodes:
        if node.role != "hyperlink" or node.ref in covered:
            continue
        found = permalink_of(node.value)
        if found is None or found[0] in seen:
            continue
        permalink, owner = found
        seen.add(permalink)
        caption, kinds = node.name, []
        while match := _TILE_KIND.search(caption):
            kinds.append(match.group(1))
            caption = caption[: match.start()]
        out.append(
            {
                "kind": "grid",
                "author": owner or page_owner,
                "caption": caption.strip() or None,
                "permalink": permalink,
                "like_count": None,
                "comment_count": None,
                "liked": None,
                "saved": None,
                "has_video": "Clip" in kinds or "/reel/" in permalink,
                "is_ad": False,
                "media_alt": None,
                "visible": node.visible,
                "refs": {"permalink": node.ref},
            }
        )
    return out
