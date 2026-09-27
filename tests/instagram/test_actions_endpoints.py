"""Write-action safety + request construction, shortcode codec, collection parsing."""

from __future__ import annotations

import json

import pytest

from heliograph.drivers.base import InstagramDriver
from heliograph.drivers.cdp import CdpDriver
from heliograph.errors import UnsafeActionError
from heliograph.instagram import endpoints as ep
from heliograph.instagram.collections import collections_from_json
from heliograph.instagram.service import InstagramService
from tests.instagram.fakes import FakeApi

WRITES = [
    ("like", ("123",), {}),
    ("unlike", ("123",), {}),
    ("save", ("123",), {}),
    ("unsave", ("123",), {}),
    ("follow", ("456",), {}),
    ("unfollow", ("456",), {}),
    ("comment", ("123", "hi"), {}),
    ("send_dm", ("hi",), {"thread_id": "t"}),
]


@pytest.mark.parametrize(("name", "args", "kwargs"), WRITES)
async def test_writes_refuse_without_confirm(name: str, args: tuple[str, ...],
                                             kwargs: dict[str, str]) -> None:
    api = FakeApi()
    with pytest.raises(UnsafeActionError):
        await getattr(InstagramService(api), name)(*args, **kwargs)
    with pytest.raises(UnsafeActionError):
        await getattr(InstagramService(api), name)(*args, confirm="yes", **kwargs)
    assert api.calls == []


async def test_write_request_construction() -> None:
    pk = ep.code_to_pk("C8abcDEF123")
    ok = {"status": "ok"}
    api = FakeApi({
        f"/api/v1/web/likes/{pk}/like/": ok,
        "/api/v1/web/likes/1/unlike/": ok,
        "/api/v1/web/save/1/save/": ok,
        "/api/v1/web/save/1/unsave/": ok,
        "/api/v1/users/web_profile_info/": {"data": {"user": {"id": "77", "username": "bob"}}},
        "/api/v1/friendships/create/77/": ok,
        "/api/v1/friendships/destroy/88/": ok,
        "/api/v1/web/comments/1/add/": ok,
        "/api/v1/direct_v2/threads/broadcast/text/": ok,
    })
    svc = InstagramService(api)
    await svc.like("https://www.instagram.com/p/C8abcDEF123/", confirm=True)
    await svc.unlike("1_999", confirm=True)
    await svc.save("1", collection_id="42", confirm=True)
    await svc.unsave("1", confirm=True)
    await svc.follow("@bob", confirm=True)
    await svc.unfollow("88", confirm=True)
    await svc.comment("1", "great post", confirm=True)
    await svc.send_dm("hello", username="bob", confirm=True)
    posts = [c for c in api.calls if c[0] == "POST"]
    assert all(c[3] is True for c in posts)  # all through the write limiter
    by_path = {c[1]: c[2] for c in posts}
    assert by_path["/api/v1/web/save/1/save/"] == {"added_collection_ids": '["42"]'}
    assert by_path["/api/v1/friendships/create/77/"]["user_id"] == "77"
    assert by_path["/api/v1/web/comments/1/add/"] == {"comment_text": "great post"}
    dm = by_path["/api/v1/direct_v2/threads/broadcast/text/"]
    assert json.loads(dm["recipient_users"]) == [["77"]] and dm["text"] == "hello"
    with pytest.raises(ValueError):
        await svc.send_dm("x", confirm=True)
    with pytest.raises(ValueError):
        await svc.comment("1", "  ", confirm=True)


@pytest.mark.parametrize(
    "pk", ["0", "1", "63", "64", "3412345678901234567", "18446744073709551615"]
)
def test_shortcode_roundtrip(pk: str) -> None:
    assert ep.code_to_pk(ep.pk_to_code(pk)) == pk


def test_shortcode_helpers() -> None:
    assert ep.code_to_pk("B") == "1" and ep.code_to_pk("BA") == "64"
    assert ep.shortcode_from_url("https://www.instagram.com/reel/AbC_-1/?igsh=x") == "AbC_-1"
    assert ep.shortcode_from_url("AbC") == "AbC"
    long_code = ep.pk_to_code("3412345678901234567")
    assert len(long_code) == 11
    assert ep.code_to_pk(long_code + "ZZZZZZZZZZ") == "3412345678901234567"
    with pytest.raises(ValueError):
        ep.code_to_pk("bad!code")
    with pytest.raises(ValueError):
        ep.shortcode_from_url("https://www.instagram.com/someone/")


def test_parse_collection_links() -> None:
    links = [
        ("/me/saved/all-posts/", "All posts"),
        ("/me/saved/trading-strats/281/", "Trading strats"),
        ("/me/saved/trading-strats/281/", "dup"),
        ("/me/saved/food/9/?x=1", ""),
        ("/other/saved/x/1/", "no"),
    ]
    got = ep.parse_collection_links(links, "me")
    assert got == [{"id": "281", "slug": "trading-strats", "name": "Trading strats"},
                   {"id": "9", "slug": "food", "name": "food"}]


def test_collections_from_json() -> None:
    blob = {"data": {"a": [{"collection_id": "5", "collection_name": "X",
                            "collection_media_count": 3}, {"collection_id": "6"}]}}
    got = collections_from_json(blob)
    assert list(got) == ["5"] and got["5"].media_count == 3


def test_cdp_driver_implements_protocol() -> None:
    assert isinstance(CdpDriver(launch=False), InstagramDriver)
