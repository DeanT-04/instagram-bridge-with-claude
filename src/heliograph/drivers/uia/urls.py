"""Instagram URL helpers shared by the UIA readers (pure)."""

from __future__ import annotations

import re
from urllib.parse import urlparse

__all__ = ["IG_URL", "RESERVED", "permalink_of", "username_from_url"]

IG_URL = re.compile(r"^https://(?:www\.)?instagram\.com/")
RESERVED = {
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
# optional owner prefix: profile grids link to /<owner>/p/<code>/ and /<owner>/reel/<code>/
_PERMALINK = re.compile(
    r"instagram\.com/(?:([A-Za-z0-9._]+)/)?(p|reel|reels|tv)/([A-Za-z0-9_-]+)/?"
)


def username_from_url(url: str | None) -> str | None:
    """``https://www.instagram.com/alice/`` or ``.../alice/reels/`` -> ``"alice"``."""
    if not url or not IG_URL.match(url):
        return None
    parts = [p for p in urlparse(url).path.split("/") if p]
    if not parts or parts[0] in RESERVED or not re.fullmatch(r"[A-Za-z0-9._]+", parts[0]):
        return None
    if len(parts) > 1 and parts[1] not in ("reels", "tagged", "saved", "followers", "following"):
        return None
    return parts[0]


def permalink_of(url: str | None) -> tuple[str, str | None] | None:
    """Canonical post URL and owner (if in the URL) for a post/reel link, else None."""
    match = _PERMALINK.search(url or "")
    if not match or "/audio/" in (url or ""):
        return None
    owner, kind, code = match.groups()
    if owner in RESERVED:
        owner = None
    return f"https://www.instagram.com/{kind}/{code}/", owner
