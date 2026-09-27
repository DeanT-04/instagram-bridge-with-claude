"""Read-only Instagram tools backed by the web API of the dedicated browser profile."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from mcp.server.fastmcp import FastMCP

from heliograph.instagram.models import Page
from heliograph.mcp.common import READ_ONLY, dump, dump_media, dump_page, dump_user, tool
from heliograph.mcp.runtime import Runtime

__all__ = ["MAX_LIMIT", "collect", "register"]

MAX_LIMIT = 100


def _limit(value: int) -> int:
    if value < 1:
        raise ValueError("limit must be at least 1")
    return min(value, MAX_LIMIT)


async def collect(
    fetch: Callable[[str | None], Awaitable[Page[Any]]], cursor: str | None, limit: int
) -> tuple[Page[Any], int]:
    """Fetch pages from ``cursor`` until ``limit`` items; returns (page, items dropped).

    The returned page's ``next_cursor`` continues after the *last fetched* page, so items
    dropped from it to honour ``limit`` are skipped when paginating onwards.
    """
    limit = _limit(limit)
    items: list[Any] = []
    page: Page[Any] = Page()
    current = cursor
    while True:
        page = await fetch(current)
        items.extend(page.items)
        if len(items) >= limit or not page.has_more or not page.next_cursor:
            break
        if page.next_cursor == current:
            break
        current = page.next_cursor
    dropped = max(0, len(items) - limit)
    return Page[Any](items=items[:limit], next_cursor=page.next_cursor,
                     has_more=page.has_more), dropped


def register(server: FastMCP, rt: Runtime) -> None:
    """Register the read tools."""

    def media_item(caption_chars: int) -> Callable[[Any], Any]:
        return lambda m: dump_media(m, caption_chars=caption_chars)

    async def media_page(fetch: Callable[[str | None], Awaitable[Page[Any]]],
                         cursor: str | None, limit: int, caption_chars: int) -> dict[str, Any]:
        page, dropped = await collect(fetch, cursor, limit)
        return dump_page(page, media_item(caption_chars), truncated=dropped)

    @tool(server, annotations=READ_ONLY)
    async def ig_whoami() -> dict[str, Any]:
        """Return the Instagram account logged in to Heliograph's dedicated browser profile.

        Fails with NotLoggedInError if the user has not signed in yet (they must run
        `heliograph login` themselves; never ask for their password)."""
        svc = await rt.service()
        return dump_user(await svc.viewer()) or {}

    @tool(server, annotations=READ_ONLY, untrusted=True)
    async def ig_get_user(username: str) -> dict[str, Any]:
        """Public profile of an account by username (without @): pk, full name,
        verified/private flags and profile URL."""
        user = await (await rt.service()).get_user(username.lstrip("@"))
        return {**(dump_user(user) or {}), "url": user.url}

    @tool(server, annotations=READ_ONLY, untrusted=True)
    async def ig_user_posts(username: str, limit: int = 12, cursor: str | None = None,
                            caption_chars: int = 300) -> dict[str, Any]:
        """Recent posts/reels of an account, newest first. Returns compact items (code,
        url, type, caption trimmed to caption_chars, counts) plus next_cursor; pass it back
        as cursor for older posts."""
        svc = await rt.service()
        count = min(_limit(limit), 33)
        return await media_page(lambda c: svc.user_medias(username.lstrip("@"), c, count),
                                cursor, limit, caption_chars)

    @tool(server, annotations=READ_ONLY, untrusted=True)
    async def ig_get_media(ref: str) -> dict[str, Any]:
        """Full details of one post or reel. `ref` may be a post/reel URL
        (https://www.instagram.com/reel/ABC123/), a shortcode (ABC123) or a numeric pk.
        Includes the full caption and the video/thumbnail CDN URLs."""
        media = await (await rt.service()).media(ref)
        out = dump_media(media, caption_chars=None, urls=True)
        if media.children:
            out["carousel"] = [dump_media(c, caption_chars=0, urls=True) for c in media.children]
        return out

    @tool(server, annotations=READ_ONLY, untrusted=True)
    async def ig_comments(ref: str, limit: int = 20, cursor: str | None = None) -> dict[str, Any]:
        """Top-level comments on a post/reel (`ref` = URL, shortcode or pk), with author,
        text, time and like count. Use next_cursor for more."""
        svc = await rt.service()
        page, dropped = await collect(lambda c: svc.comments(ref, c), cursor, limit)
        return dump_page(page, lambda c: {"id": c.id, "user": c.user.username if c.user
                                          else None, "text": c.text,
                                          "created_at": c.created_at, "likes": c.like_count},
                         truncated=dropped)

    @tool(server, annotations=READ_ONLY, untrusted=True)
    async def ig_search(query: str, limit: int = 10) -> dict[str, Any]:
        """Instagram top search: matching users, hashtags and places (up to `limit` of each)."""
        res = await (await rt.service()).search(query)
        n = _limit(limit)
        return {"users": [dump_user(u) for u in res.users[:n]],
                "hashtags": [dump(h) for h in res.hashtags[:n]],
                "places": [dump(p) for p in res.places[:n]]}

    @tool(server, annotations=READ_ONLY, untrusted=True)
    async def ig_timeline(limit: int = 12, cursor: str | None = None,
                          caption_chars: int = 300) -> dict[str, Any]:
        """The user's home feed (posts from accounts they follow; ads/suggestions without
        media are skipped). Paginate with next_cursor."""
        svc = await rt.service()
        return await media_page(svc.timeline, cursor, limit, caption_chars)

    @tool(server, annotations=READ_ONLY, untrusted=True)
    async def ig_reels_feed(limit: int = 10, cursor: str | None = None,
                            caption_chars: int = 300) -> dict[str, Any]:
        """The Reels discovery feed (what the Reels tab would show). Paginate with
        next_cursor."""
        svc = await rt.service()
        return await media_page(svc.reels, cursor, limit, caption_chars)

    @tool(server, annotations=READ_ONLY, untrusted=True)
    async def ig_explore(limit: int = 20, cursor: str | None = None,
                         caption_chars: int = 200) -> dict[str, Any]:
        """Posts from the Explore grid. Paginate with next_cursor."""
        svc = await rt.service()
        return await media_page(svc.explore, cursor, limit, caption_chars)

    @tool(server, annotations=READ_ONLY, untrusted=True)
    async def ig_inbox(limit: int = 10, cursor: str | None = None) -> dict[str, Any]:
        """Direct-message threads with participants and the latest message preview.
        Use a thread's id with ig_thread to read it. Private data: only summarise what
        the user asked for."""
        svc = await rt.service()
        n = _limit(limit)
        page, dropped = await collect(lambda c: svc.inbox(c, min(n, 20)), cursor, n)
        return dump_page(page, _thread, truncated=dropped)

    @tool(server, annotations=READ_ONLY, untrusted=True)
    async def ig_thread(
        thread_id: str, limit: int = 20, cursor: str | None = None
    ) -> dict[str, Any]:
        """Messages of one DM thread (newest first). Pass the returned oldest_cursor as
        cursor to page back in time."""
        thread = await (await rt.service()).thread(thread_id, cursor, _limit(limit))
        return _thread(thread)

    @tool(server, annotations=READ_ONLY, untrusted=True)
    async def ig_activity(limit: int = 20) -> dict[str, Any]:
        """Recent notifications: likes, follows, comments and mentions on the user's
        account (newest first)."""
        items = await (await rt.service()).activity()
        n = _limit(limit)
        return {"count": min(n, len(items)), "items": [dump(i) for i in items[:n]]}


def _thread(t: Any) -> dict[str, Any]:
    return {
        "id": t.id,
        "title": t.title,
        "users": [u.username for u in t.users],
        "is_group": t.is_group,
        "last_activity": t.last_activity,
        "messages": [
            {k: v for k, v in {"id": m.id, "user_id": m.user_id, "type": m.item_type,
                               "text": m.text, "time": m.timestamp,
                               "media_code": m.media_code}.items() if v is not None}
            for m in t.messages
        ],
        "oldest_cursor": t.oldest_cursor,
        "has_older": t.has_older,
    }
