"""Instagram web API paths and identifier helpers.

Paths are relative to ``https://www.instagram.com`` and are used with
:class:`heliograph.drivers.cdp.webapi.WebApiClient`. Each constant notes whether it was
verified against a live logged-in web session (status noted in the service docstrings).
"""

from __future__ import annotations

import re
from urllib.parse import quote, urlparse

__all__ = [
    "code_to_pk",
    "is_instagram_url",
    "media_id_to_pk",
    "parse_collection_links",
    "pk_to_code",
    "shortcode_from_url",
]

_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
_INDEX = {c: i for i, c in enumerate(_ALPHABET)}
_SHORTCODE_URL = re.compile(r"/(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)")
_SEGMENT = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_IG_HOSTS = frozenset({"www.instagram.com", "instagram.com"})
# Characters that URL parsers disagree on (WHATWG treats a backslash as "/" and strips
# tabs/newlines; Python's urllib does neither): a URL containing any of them is refused.
_URL_BAD_CHARS = re.compile(r"[\x00-\x20\x7f\\]")


def is_instagram_url(url: str) -> bool:
    """True only for a plain ``https://www.instagram.com/...`` (or bare-domain) URL.

    Strict on purpose, because the result gates what a real, logged-in browser opens:
    no userinfo, no non-default port, ASCII only, and no characters on which Python's
    ``urlparse`` and the browser's URL parser disagree (``https://evil.com<backslash>@``
    ``www.instagram.com/`` is ``evil.com`` to Chromium).
    """
    if not isinstance(url, str) or not url.isascii() or _URL_BAD_CHARS.search(url):
        return False
    p = urlparse(url)
    if p.scheme != "https" or p.username is not None or p.password is not None:
        return False
    try:
        port = p.port
    except ValueError:
        return False
    if port not in (None, 443):
        return False
    host = p.hostname or ""
    return host in _IG_HOSTS and p.netloc.lower() in (host, f"{host}:443")


def _seg(value: str | int) -> str:
    """Validate one URL path segment taken from caller input (ids, pks, thread ids)."""
    text = str(value)
    if not _SEGMENT.match(text):
        raise ValueError(f"Invalid Instagram identifier {text!r}")
    return text


def code_to_pk(code: str) -> str:
    """Convert a post shortcode (``DAbc12_x-Y``) to its numeric media pk (as str).

    Long private-post codes carry an extra suffix after the first 11 characters; only the
    first 11 encode the pk.
    """
    code = code.strip()
    if not code or any(c not in _INDEX for c in code):
        raise ValueError(f"Invalid shortcode {code!r}")
    value = 0
    for ch in code[:11] if len(code) > 11 else code:
        value = value * 64 + _INDEX[ch]
    return str(value)


def pk_to_code(pk: str | int) -> str:
    """Inverse of :func:`code_to_pk`."""
    value = int(str(pk).split("_")[0])
    if value < 0:
        raise ValueError("pk must be non-negative")
    out = ""
    while True:
        value, rem = divmod(value, 64)
        out = _ALPHABET[rem] + out
        if value == 0:
            return out


def media_id_to_pk(media_id: str) -> str:
    """``"<pk>_<owner_id>"`` → ``"<pk>"``."""
    return str(media_id).split("_")[0]


def shortcode_from_url(url_or_code: str) -> str:
    """Accept a post/reel URL or a bare shortcode and return the shortcode."""
    if "/" not in url_or_code:
        return url_or_code.strip()
    match = _SHORTCODE_URL.search(urlparse(url_or_code).path)
    if not match:
        raise ValueError(f"No post shortcode in {url_or_code!r}")
    return match.group(1)


def parse_collection_links(hrefs: list[tuple[str, str]], username: str) -> list[dict[str, str]]:
    """Extract ``{"id","slug","name"}`` from ``(href, text)`` anchor pairs.

    Matches ``/<username>/saved/<slug>/<numeric id>/`` (the "All posts" pseudo collection
    ``/saved/all-posts/`` has no id and is skipped).
    """
    pat = re.compile(rf"^/{re.escape(username)}/saved/([^/]+)/(\d+)/?$", re.IGNORECASE)
    out: dict[str, dict[str, str]] = {}
    for href, text in hrefs:
        m = pat.match(urlparse(href).path)
        if m and m.group(2) not in out:
            name = (text or "").strip().split("\n")[0] or m.group(1).replace("-", " ")
            out[m.group(2)] = {"id": m.group(2), "slug": m.group(1), "name": name}
    return list(out.values())


# -- paths ------------------------------------------------------------------------
def user_info(pk: str) -> str:
    return f"/api/v1/users/{_seg(pk)}/info/"


def web_profile_info() -> str:
    return "/api/v1/users/web_profile_info/"


def user_feed(username: str) -> str:
    return f"/api/v1/feed/user/{quote(username, safe='')}/username/"


def user_feed_by_pk(pk: str) -> str:
    return f"/api/v1/feed/user/{_seg(pk)}/"


def media_info(pk: str) -> str:
    return f"/api/v1/media/{_seg(pk)}/info/"


def media_comments(pk: str) -> str:
    return f"/api/v1/media/{_seg(pk)}/comments/"


WEB_FORM_DATA = "/api/v1/accounts/edit/web_form_data/"
SAVED_POSTS = "/api/v1/feed/saved/posts/"
TIMELINE = "/api/v1/feed/timeline/"
REELS_TRAY = "/api/v1/feed/reels_tray/"
CLIPS_DISCOVER = "/api/v1/clips/discover/"
EXPLORE_GRID = "/api/v1/discover/web/explore_grid/"
TOPSEARCH = "/api/v1/web/search/topsearch/"
INBOX = "/api/v1/direct_v2/inbox/"
NEWS_INBOX = "/api/v1/news/inbox/"


def collection_posts(collection_id: str) -> str:
    return f"/api/v1/feed/collection/{_seg(collection_id)}/posts/"


def direct_thread(thread_id: str) -> str:
    return f"/api/v1/direct_v2/threads/{_seg(thread_id)}/"


# -- write paths (not live-verified by design) ----------------------------------------
def like(pk: str) -> str:
    return f"/api/v1/web/likes/{_seg(pk)}/like/"


def unlike(pk: str) -> str:
    return f"/api/v1/web/likes/{_seg(pk)}/unlike/"


def save(pk: str) -> str:
    return f"/api/v1/web/save/{_seg(pk)}/save/"


def unsave(pk: str) -> str:
    return f"/api/v1/web/save/{_seg(pk)}/unsave/"


def follow(user_pk: str) -> str:
    return f"/api/v1/friendships/create/{_seg(user_pk)}/"


def unfollow(user_pk: str) -> str:
    return f"/api/v1/friendships/destroy/{_seg(user_pk)}/"


def add_comment(pk: str) -> str:
    return f"/api/v1/web/comments/{_seg(pk)}/add/"


DIRECT_BROADCAST_TEXT = "/api/v1/direct_v2/threads/broadcast/text/"

