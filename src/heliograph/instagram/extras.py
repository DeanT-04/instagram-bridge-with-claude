"""Models used by :mod:`heliograph.instagram.service` that are not in ``models.py``.

Search results, direct-message threads/messages and activity items. Same forgiving style
as :mod:`heliograph.instagram.models` (tolerate missing keys, keep ``raw``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from heliograph.instagram.models import User

__all__ = ["ActivityItem", "DirectMessage", "DirectThread", "Hashtag", "Place", "SearchResults"]


def _ts_any(value: Any) -> datetime | None:
    """Parse seconds, milliseconds or microseconds epoch timestamps."""
    try:
        v = int(value)
    except (TypeError, ValueError):
        return None
    while v > 10**11:  # micro/milliseconds → seconds
        v //= 1000
    try:
        return datetime.fromtimestamp(v, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _sub(data: dict[str, Any], key: str) -> dict[str, Any]:
    """``data[key]`` if it is a dict, else ``data`` itself."""
    value = data.get(key)
    return value if isinstance(value, dict) else data


class _Model(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class Hashtag(_Model):
    """A hashtag search hit."""

    id: str | None = None
    name: str
    media_count: int | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Hashtag:
        """Parse a topsearch ``hashtags[].hashtag`` object."""
        d = _sub(data, "hashtag")
        return cls(id=str(d.get("id")) if d.get("id") else None, name=str(d.get("name") or ""),
                   media_count=d.get("media_count"))


class Place(_Model):
    """A location search hit."""

    pk: str | None = None
    name: str
    address: str | None = None
    city: str | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Place:
        """Parse a topsearch ``places[].place`` object."""
        d = _sub(data, "place")
        loc = _sub(d, "location")
        return cls(pk=str(loc.get("pk")) if loc.get("pk") else None,
                   name=str(d.get("title") or loc.get("name") or ""),
                   address=loc.get("address") or None, city=loc.get("city") or None)


class SearchResults(_Model):
    """Top search results."""

    users: list[User] = Field(default_factory=list)
    hashtags: list[Hashtag] = Field(default_factory=list)
    places: list[Place] = Field(default_factory=list)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> SearchResults:
        """Parse ``/api/v1/web/search/topsearch/``."""
        def _items(key: str) -> list[dict[str, Any]]:
            return [i for i in data.get(key) or [] if isinstance(i, dict)]

        return cls(
            users=[User.from_api(i.get("user") or i) for i in _items("users")],
            hashtags=[Hashtag.from_api(i) for i in _items("hashtags")],
            places=[Place.from_api(i) for i in _items("places")],
        )


class DirectMessage(_Model):
    """One item in a DM thread."""

    id: str
    user_id: str | None = None
    item_type: str | None = None
    text: str | None = None
    timestamp: datetime | None = None
    media_code: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict, repr=False, exclude=True)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> DirectMessage:
        """Parse a ``direct_v2`` thread item (text, shared post/reel, link...)."""
        shared = data.get("clip") or data.get("media_share") or {}
        if isinstance(shared, dict) and isinstance(shared.get("clip"), dict):
            shared = shared["clip"]
        link = _sub(data, "link") if isinstance(data.get("link"), dict) else {}
        return cls(
            id=str(data.get("item_id") or data.get("id") or ""),
            user_id=str(data["user_id"]) if data.get("user_id") else None,
            item_type=data.get("item_type"),
            text=data.get("text") or link.get("text") or None,
            timestamp=_ts_any(data.get("timestamp")),
            media_code=shared.get("code") if isinstance(shared, dict) else None,
            raw=data,
        )


class DirectThread(_Model):
    """A DM conversation."""

    id: str
    title: str | None = None
    users: list[User] = Field(default_factory=list)
    is_group: bool = False
    last_activity: datetime | None = None
    messages: list[DirectMessage] = Field(default_factory=list)
    oldest_cursor: str | None = None
    has_older: bool = False

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> DirectThread:
        """Parse an ``inbox.threads[]`` item or a ``threads/{id}`` ``thread`` object."""
        return cls(
            id=str(data.get("thread_id") or data.get("thread_v2_id") or ""),
            title=data.get("thread_title") or None,
            users=[User.from_api(u) for u in data.get("users") or [] if isinstance(u, dict)],
            is_group=bool(data.get("is_group", False)),
            last_activity=_ts_any(data.get("last_activity_at")),
            messages=[DirectMessage.from_api(i) for i in data.get("items") or []
                      if isinstance(i, dict)],
            oldest_cursor=data.get("oldest_cursor") or None,
            has_older=bool(data.get("has_older", False)),
        )


class ActivityItem(_Model):
    """A notification/activity entry."""

    type: str | None = None
    text: str | None = None
    timestamp: datetime | None = None
    user: User | None = None
    media_code: str | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> ActivityItem:
        """Parse a ``news/inbox`` story (``{"story_type", "args": {...}}``)."""
        args = _sub(data, "args") if isinstance(data.get("args"), dict) else {}
        medias = [m for m in args.get("media") or [] if isinstance(m, dict)]
        username = args.get("profile_name")
        return cls(
            type=str(data.get("story_type") or data.get("type") or "") or None,
            text=args.get("rich_text") or args.get("text") or None,
            timestamp=_ts_any(args.get("timestamp")),
            user=User(pk=str(args.get("profile_id") or "") or None, username=username)
            if username else None,
            media_code=(medias[0].get("shortcode") or medias[0].get("code")) if medias else None,
        )
