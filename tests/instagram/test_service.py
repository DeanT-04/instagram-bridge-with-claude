"""InstagramService read operations against a fake API client (no network)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from heliograph.errors import HeliographError
from heliograph.instagram.collections import CollectionLister
from heliograph.instagram.endpoints import code_to_pk
from heliograph.instagram.models import Collection, MediaType
from heliograph.instagram.service import InstagramService
from tests.instagram.fakes import FakeApi

FIXTURES = json.loads((Path(__file__).parents[1] / "fixtures" / "media_items.json").read_text())
REEL = FIXTURES["reel"]


def _paged(pages: dict[str | None, dict[str, Any]]) -> Any:
    return lambda params: pages[params.get("max_id")]


async def test_saved_medias_parses_wrapper_and_cursor() -> None:
    api = FakeApi({"/api/v1/feed/saved/posts/": {
        "items": [{"media": REEL}], "more_available": True, "next_max_id": "c2"}})
    page = await InstagramService(api).saved_medias()
    assert page.items[0].media_type is MediaType.REEL and page.items[0].code == REEL["code"]
    assert (page.next_cursor, page.has_more) == ("c2", True)
    assert api.calls[0][2] == {"max_id": None}


async def test_iter_collection_follows_pages_and_respects_limit() -> None:
    path = "/api/v1/feed/collection/42/posts/"
    api = FakeApi({path: _paged({
        None: {"items": [{"media": REEL}] * 2, "more_available": True, "next_max_id": "p2"},
        "p2": {"items": [{"media": REEL}] * 2, "more_available": False, "next_max_id": None},
    })})
    svc = InstagramService(api)
    assert len([m async for m in svc.iter_collection("42")]) == 4
    assert len([m async for m in svc.iter_collection("42", limit=3)]) == 3
    assert [c[2]["max_id"] for c in api.calls] == [None, "p2", None, "p2"]


async def test_collection_by_name_uses_lister() -> None:
    class Lister(CollectionLister):
        def __init__(self) -> None:
            pass

        async def list(self, username: str) -> list[Collection]:
            assert username == "me"
            return [Collection(id="7", name="Trading strats")]

    api = FakeApi({
        "/api/v1/accounts/edit/web_form_data/": {"form_data": {"username": "me"}},
        "/api/v1/feed/collection/7/posts/": {"items": [{"media": REEL}] * 3,
                                             "more_available": False},
    })
    svc = InstagramService(api, collections=Lister())
    page = await svc.collection_medias("trading-strats", limit=2)
    assert len(page.items) == 2 and not page.has_more
    with pytest.raises(HeliographError):
        await svc.resolve_collection("nope")


async def test_list_collections_without_driver_errors() -> None:
    with pytest.raises(HeliographError):
        await InstagramService(FakeApi()).list_collections()


async def test_media_resolves_shortcode_url() -> None:
    pk = code_to_pk(REEL["code"])
    api = FakeApi({f"/api/v1/media/{pk}/info/": {"items": [REEL]}})
    media = await InstagramService(api).media(f"https://www.instagram.com/reel/{REEL['code']}/")
    assert media.code == REEL["code"]


async def test_viewer_and_get_user() -> None:
    api = FakeApi({
        "/api/v1/accounts/edit/web_form_data/": {"form_data": {"username": "me",
                                                               "first_name": "Me"}},
        "/api/v1/users/web_profile_info/": {"data": {"user": {"id": "5", "username": "bob"}}},
    })
    svc = InstagramService(api, viewer_id="1")
    v = await svc.viewer()
    assert (v.pk, v.username, v.full_name) == ("1", "me", "Me")
    await svc.viewer()
    assert len(api.calls) == 1  # cached
    assert (await svc.get_user("bob")).pk == "5"


async def test_user_medias_and_comments() -> None:
    api = FakeApi({
        "/api/v1/feed/user/bob/username/": {"items": [REEL], "more_available": False},
        f"/api/v1/media/{REEL['pk']}/comments/": {
            "comments": [{"pk": 9, "text": "nice", "user": {"username": "a"}}],
            "next_min_id": "m2"},
    })
    svc = InstagramService(api)
    assert len((await svc.user_medias("bob", count=5)).items) == 1
    comments = await svc.comments(REEL["pk"])
    assert comments.items[0].text == "nice" and comments.next_cursor == "m2"


async def test_search_inbox_thread_activity() -> None:
    api = FakeApi({
        "/api/v1/web/search/topsearch/": {
            "users": [{"position": 0, "user": {"pk": "1", "username": "u"}}],
            "hashtags": [{"hashtag": {"id": 3, "name": "trading", "media_count": 10}}],
            "places": [{"place": {"title": "Paris", "location": {"pk": 8, "city": "Paris"}}}]},
        "/api/v1/direct_v2/inbox/": {"inbox": {"threads": [{
            "thread_id": "t1", "thread_title": "Bob", "users": [{"pk": 2, "username": "bob"}],
            "items": [{"item_id": "i1", "item_type": "text", "text": "yo",
                       "timestamp": 1719000000000000}]}], "has_older": True,
            "oldest_cursor": "o1"}},
        "/api/v1/direct_v2/threads/t1/": {"thread": {"thread_id": "t1", "items": [
            {"item_id": "i2", "item_type": "clip", "clip": {"clip": {"code": "ABC"}}}]}},
        "/api/v1/news/inbox/": {"new_stories": [{"story_type": 101, "args": {
            "rich_text": "x liked", "profile_name": "x", "timestamp": 1719000000,
            "media": [{"shortcode": "S1"}]}}], "old_stories": []},
    })
    svc = InstagramService(api)
    res = await svc.search("trading")
    assert res.users[0].username == "u" and res.hashtags[0].name == "trading"
    assert res.places[0].name == "Paris"
    inbox = await svc.inbox(limit=5)
    assert inbox.next_cursor == "o1" and inbox.items[0].messages[0].text == "yo"
    assert inbox.items[0].messages[0].timestamp is not None
    assert inbox.items[0].messages[0].timestamp.year == 2024
    thread = await svc.thread("t1")
    assert thread.messages[0].media_code == "ABC"
    act = await svc.activity()
    assert act[0].user is not None and act[0].media_code == "S1"


async def test_timeline_reels_explore_shapes() -> None:
    api = FakeApi({
        "/api/v1/feed/timeline/": {"feed_items": [{"media_or_ad": REEL}, {"suggested_users": {}}],
                                   "more_available": True, "next_max_id": "n"},
        "/api/v1/clips/discover/": {"items": [{"media": REEL}],
                                    "paging_info": {"max_id": "q", "more_available": True}},
        "/api/v1/discover/web/explore_grid/": {"sectional_items": [
            {"layout_content": {"medias": [{"media": REEL}, {"media": REEL}]}}],
            "more_available": False},
    })
    svc = InstagramService(api)
    tl = await svc.timeline()
    assert len(tl.items) == 1 and tl.next_cursor == "n"
    assert api.calls[0][3] is False  # read-only POST uses the read limiter
    assert (await svc.reels()).next_cursor == "q"
    assert len((await svc.explore()).items) == 2
