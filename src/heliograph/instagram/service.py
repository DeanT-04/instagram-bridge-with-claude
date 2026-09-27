"""High-level, async Instagram operations returning Heliograph models.

``InstagramService`` composes a web-API client (same-origin fetch inside the page) and,
for the few things only the page can do (listing saved collections), the CDP driver.
Write operations live in :class:`~heliograph.instagram.actions.WriteActions` and demand
``confirm=True``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol

from heliograph.errors import HeliographError
from heliograph.eye import span, traced
from heliograph.instagram import endpoints as ep
from heliograph.instagram.actions import WriteActions
from heliograph.instagram.collections import CollectionLister
from heliograph.instagram.extras import ActivityItem, DirectThread, SearchResults
from heliograph.instagram.models import Collection, Comment, Media, Page, User

__all__ = ["ApiClient", "InstagramService"]


class ApiClient(Protocol):
    """What the service needs from :class:`~heliograph.drivers.cdp.webapi.WebApiClient`."""

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]: ...

    async def post(
        self, path: str, data: dict[str, Any] | None = None, *,
        params: dict[str, Any] | None = None, write: bool = True,
    ) -> dict[str, Any]: ...


def _media_page(data: dict[str, Any], key: str = "items") -> Page[Media]:
    items = [i for i in data.get(key) or [] if isinstance(i, dict)]
    medias = [Media.from_api(i) for i in items if isinstance(i.get("media", i), dict)]
    cursor = data.get("next_max_id") or data.get("max_id")
    more = bool(data.get("more_available", cursor is not None))
    return Page[Media](items=medias, next_cursor=str(cursor) if cursor and more else None,
                       has_more=more and bool(cursor))


class InstagramService(WriteActions):
    """Instagram operations for one logged-in account.

    Args:
        api: Web API client (normally ``CdpDriver.api()``).
        collections: Optional page-based collection lister (normally built from the driver).
        viewer_id: Numeric id of the logged-in user if already known (``ds_user_id``).
    """

    def __init__(
        self,
        api: ApiClient,
        *,
        collections: CollectionLister | None = None,
        viewer_id: str | None = None,
    ) -> None:
        self.api = api
        self._collections = collections
        self._viewer_id = viewer_id
        self._viewer: User | None = None

    @classmethod
    async def from_driver(cls, driver: Any) -> InstagramService:
        """Build a service from a connected :class:`~heliograph.drivers.cdp.CdpDriver`."""
        await driver.ensure_ready()
        return cls(driver.api(), collections=CollectionLister(driver),
                   viewer_id=await driver.viewer_id())

    # -- users ------------------------------------------------------------------------
    @traced(name="ig.viewer")
    async def viewer(self) -> User:
        """The logged-in account."""
        if self._viewer is None:
            data = await self.api.get(ep.WEB_FORM_DATA)
            form = data.get("form_data") or {}
            if not form.get("username"):
                raise HeliographError("Could not determine the logged-in user")
            self._viewer = User(pk=self._viewer_id, username=str(form["username"]),
                                full_name=form.get("first_name") or None)
        return self._viewer

    @traced(name="ig.get_user")
    async def get_user(self, username: str) -> User:
        """Public profile of ``username``."""
        data = await self.api.get(ep.web_profile_info(), {"username": username})
        user = (data.get("data") or {}).get("user")
        if not isinstance(user, dict):
            raise HeliographError(f"User {username!r} not found")
        return User.from_api(user)

    @traced(name="ig.user_medias")
    async def user_medias(
        self, username: str, cursor: str | None = None, count: int = 12
    ) -> Page[Media]:
        """Posts of ``username`` (newest first)."""
        data = await self.api.get(ep.user_feed(username), {"count": count, "max_id": cursor})
        return _media_page(data)

    # -- media ------------------------------------------------------------------------
    @traced(name="ig.media")
    async def media(self, ref: str) -> Media:
        """Full info for a post/reel given its URL, shortcode or numeric pk."""
        data = await self.api.get(ep.media_info(self.media_pk(ref)))
        items = data.get("items") or []
        if not items:
            raise HeliographError(f"Media {ref!r} not found")
        return Media.from_api(items[0])

    async def media_by_shortcode(self, code: str) -> Media:
        """Alias of :meth:`media` for a shortcode."""
        return await self.media(code)

    @traced(name="ig.comments")
    async def comments(self, ref: str, cursor: str | None = None) -> Page[Comment]:
        """Top-level comments on a post (pass ``next_cursor`` for older ones)."""
        data = await self.api.get(ep.media_comments(self.media_pk(ref)), {
            "can_support_threading": True, "permalink_enabled": False, "min_id": cursor})
        comments = [Comment.from_api(c) for c in data.get("comments") or [] if isinstance(c, dict)]
        nxt = data.get("next_min_id") or data.get("next_max_id")
        return Page[Comment](items=comments, next_cursor=str(nxt) if nxt else None,
                             has_more=bool(nxt))

    # -- saved & collections -----------------------------------------------------------
    @traced(name="ig.saved_medias")
    async def saved_medias(self, cursor: str | None = None) -> Page[Media]:
        """All saved posts (the "All posts" view)."""
        return _media_page(await self.api.get(ep.SAVED_POSTS, {"max_id": cursor}))

    @traced(name="ig.list_collections")
    async def list_collections(self) -> list[Collection]:
        """Saved-post collections of the logged-in user."""
        if self._collections is None:
            raise HeliographError("Listing collections needs the CDP driver (page access)")
        return await self._collections.list((await self.viewer()).username)

    async def resolve_collection(self, collection: str) -> str:
        """Return the numeric id for a collection id, name or slug (case-insensitive)."""
        if collection.isdigit():
            return collection
        wanted = collection.strip().lower()
        for c in await self.list_collections():
            if wanted in (c.name.lower(), c.name.lower().replace(" ", "-")):
                return c.id
        raise HeliographError(f"No saved collection named {collection!r}")

    @traced(name="ig.collection_medias")
    async def collection_medias(
        self, collection: str, cursor: str | None = None, limit: int | None = None
    ) -> Page[Media]:
        """One page of a collection (by id or name); ``limit`` truncates the page."""
        cid = await self.resolve_collection(collection)
        page = _media_page(await self.api.get(ep.collection_posts(cid), {"max_id": cursor}))
        if limit is not None and len(page.items) > limit:
            page.items = page.items[:limit]
        return page

    async def iter_collection(
        self, collection: str, limit: int | None = None
    ) -> AsyncIterator[Media]:
        """Yield every media in a collection, following pagination up to ``limit`` items."""
        async for m in self._iterate(lambda c: self.collection_medias(collection, c), limit):
            yield m

    async def iter_saved(self, limit: int | None = None) -> AsyncIterator[Media]:
        """Yield every saved media up to ``limit``."""
        async for m in self._iterate(self.saved_medias, limit):
            yield m

    async def _iterate(self, fetch: Any, limit: int | None) -> AsyncIterator[Media]:
        cursor: str | None = None
        seen = 0
        async with span("ig.iterate", limit=limit) as s:
            while True:
                page: Page[Media] = await fetch(cursor)
                for m in page.items:
                    if limit is not None and seen >= limit:
                        s.set(yielded=seen)
                        return
                    seen += 1
                    yield m
                if not page.has_more or not page.next_cursor or page.next_cursor == cursor:
                    s.set(yielded=seen)
                    return
                cursor = page.next_cursor

    # -- feeds, discovery ---------------------------------------------------------------
    @traced(name="ig.timeline")
    async def timeline(self, cursor: str | None = None) -> Page[Media]:
        """Home feed posts (suggestions and ads without media are skipped)."""
        data = await self.api.post(ep.TIMELINE, {"max_id": cursor, "is_async_ads_rti": 0,
                                                 "reason": "cold_start_fetch"}, write=False)
        items = [i.get("media_or_ad") or i for i in data.get("feed_items") or data.get("items")
                 or [] if isinstance(i, dict)]
        medias = [Media.from_api(i) for i in items if i.get("pk") or i.get("code")]
        cur = data.get("next_max_id")
        return Page[Media](items=medias, next_cursor=cur, has_more=bool(
            data.get("more_available") and cur))

    @traced(name="ig.reels")
    async def reels(self, cursor: str | None = None) -> Page[Media]:
        """Reels discovery feed."""
        data = await self.api.post(ep.CLIPS_DISCOVER, {"max_id": cursor}, write=False)
        items = [i.get("media") or i for i in data.get("items") or [] if isinstance(i, dict)]
        cur = (data.get("paging_info") or {}).get("max_id") or data.get("next_max_id")
        more = (data.get("paging_info") or {}).get("more_available", bool(cur))
        return Page[Media](items=[Media.from_api(i) for i in items],
                           next_cursor=cur if more else None, has_more=bool(more and cur))

    @traced(name="ig.explore")
    async def explore(self, cursor: str | None = None) -> Page[Media]:
        """Explore grid medias."""
        data = await self.api.get(ep.EXPLORE_GRID, {"max_id": cursor, "is_prefetch": False,
                                                    "include_fixed_destinations": True})
        medias: list[Media] = []
        for section in data.get("sectional_items") or []:
            for m in _walk_medias(section):
                medias.append(Media.from_api(m))
        cur = data.get("next_max_id")
        return Page[Media](items=medias, next_cursor=str(cur) if cur else None,
                           has_more=bool(data.get("more_available") and cur))

    @traced(name="ig.search")
    async def search(self, query: str) -> SearchResults:
        """Top search: users, hashtags and places."""
        return SearchResults.from_api(await self.api.get(
            ep.TOPSEARCH, {"query": query, "context": "blended"}))

    # -- direct & activity ---------------------------------------------------------------
    @traced(name="ig.inbox")
    async def inbox(self, cursor: str | None = None, limit: int = 10) -> Page[DirectThread]:
        """DM threads (latest message preview included)."""
        data = await self.api.get(ep.INBOX, {"persistentBadging": True, "limit": limit,
                                             "thread_message_limit": 1, "cursor": cursor})
        inbox = data.get("inbox") or {}
        threads = [DirectThread.from_api(t) for t in inbox.get("threads") or []]
        cur = inbox.get("oldest_cursor")
        return Page[DirectThread](items=threads, next_cursor=cur if inbox.get("has_older")
                                  else None, has_more=bool(inbox.get("has_older")))

    @traced(name="ig.thread")
    async def thread(self, thread_id: str, cursor: str | None = None,
                     limit: int = 20) -> DirectThread:
        """Messages of one DM thread (newest first; ``oldest_cursor`` pages back)."""
        data = await self.api.get(ep.direct_thread(thread_id), {"limit": limit, "cursor": cursor})
        return DirectThread.from_api(data.get("thread") or {})

    @traced(name="ig.activity")
    async def activity(self) -> list[ActivityItem]:
        """Recent notifications (likes, follows, comments, mentions)."""
        data = await self.api.get(ep.NEWS_INBOX)
        stories = (data.get("new_stories") or []) + (data.get("old_stories") or [])
        return [ActivityItem.from_api(s) for s in stories if isinstance(s, dict)]


def _walk_medias(node: Any, depth: int = 0) -> list[dict[str, Any]]:
    """Find ``{"media": {...}}`` objects anywhere in an explore section."""
    if depth > 6:
        return []
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        media = node.get("media")
        if isinstance(media, dict) and (media.get("pk") or media.get("code")):
            return [media]
        for v in node.values():
            found += _walk_medias(v, depth + 1)
    elif isinstance(node, list):
        for v in node:
            found += _walk_medias(v, depth + 1)
    return found
