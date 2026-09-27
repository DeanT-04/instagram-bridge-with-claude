from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from heliograph.instagram.models import (
    Collection,
    Comment,
    Media,
    MediaType,
    Page,
    User,
)

FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "media_items.json").read_text())


@pytest.fixture
def items() -> dict[str, Any]:
    return FIXTURES


def test_reel(items: dict[str, Any]) -> None:
    m = Media.from_api(items["reel"])
    assert m.media_type is MediaType.REEL and m.is_video
    assert m.id == "3412345678901234567_123456789" and m.pk == "3412345678901234567"
    assert m.code == m.shortcode == "C8abcDEF123"
    assert m.url == "https://www.instagram.com/reel/C8abcDEF123/"
    assert m.caption == "My 3-step breakout strategy #trading"
    assert m.taken_at == datetime.fromtimestamp(1719000000, tz=UTC)
    assert m.owner == User(pk="123456789", username="trader.joe", full_name="Joe Trader",
                           is_verified=True)
    assert (m.like_count, m.comment_count, m.play_count) == (1520, 87, 45000)
    assert m.video_url == "https://scontent.cdninstagram.com/v/high.mp4"  # largest wins
    assert m.video_duration == 42.5
    assert m.thumbnail_url == "https://scontent.cdninstagram.com/v/thumb_big.jpg"
    assert [v.kind for v in m.videos] == ["video", "video"]


def test_raw_kept_but_hidden(items: dict[str, Any]) -> None:
    m = Media.from_api(items["reel"])
    assert m.raw["code"] == "C8abcDEF123"
    assert "raw" not in repr(m) and "raw" not in m.model_dump()


def test_photo_coerces_types(items: dict[str, Any]) -> None:
    m = Media.from_api(items["photo"])
    assert m.media_type is MediaType.PHOTO and not m.is_video
    assert m.pk == "111" and m.like_count == 12 and m.caption is None
    assert m.url == "https://www.instagram.com/p/Cphoto1/"
    assert m.owner is not None and m.owner.pk == "222"
    assert m.taken_at is not None and m.taken_at.year == 2023


def test_carousel_children(items: dict[str, Any]) -> None:
    m = Media.from_api(items["carousel"])
    assert m.media_type is MediaType.CAROUSEL
    assert [c.media_type for c in m.children] == [MediaType.PHOTO, MediaType.VIDEO]
    assert m.children[1].video_url == "https://scontent.cdninstagram.com/c2.mp4"
    assert m.children[1].video_duration == 9.0
    assert m.thumbnail_url == "https://scontent.cdninstagram.com/c1.jpg"


def test_saved_wrapper_unwrapped(items: dict[str, Any]) -> None:
    m = Media.from_api(items["saved_wrapper"])
    assert m.pk == "555" and m.media_type is MediaType.VIDEO
    assert m.videos[0].width is None


def test_minimal_and_play_count_fallback(items: dict[str, Any]) -> None:
    m = Media.from_api(items["minimal"])
    assert m.id == "42" and m.owner is None and m.url is None and m.images == []
    m2 = Media.from_api({"pk": 1, "ig_play_count": 7, "media_type": "garbage"})
    assert m2.play_count == 7 and m2.media_type is MediaType.PHOTO


def test_user_collection_comment() -> None:
    assert User.from_api({"username": "x"}).url == "https://www.instagram.com/x/"
    assert User.from_api(None).username == "unknown"
    c = Collection.from_api(
        {"collection_id": "17", "collection_name": "Trading", "collection_media_count": 3}
    )
    assert (c.id, c.name, c.media_count) == ("17", "Trading", 3)
    cm = Comment.from_api(
        {"pk": 5, "text": "nice", "user": {"username": "u"}, "created_at": 1700000000,
         "comment_like_count": 2}
    )
    assert cm.id == "5" and cm.user is not None and cm.like_count == 2


def test_page_generic_roundtrip(items: dict[str, Any]) -> None:
    page = Page[Media](items=[Media.from_api(items["reel"])], next_cursor="abc", has_more=True)
    again = Page[Media].model_validate_json(page.model_dump_json())
    assert again.items[0].code == "C8abcDEF123" and again.has_more
    assert Page[Collection]().items == []
