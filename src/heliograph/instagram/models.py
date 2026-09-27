"""Pydantic v2 models for Instagram entities, with parsers for the web API v1 payloads.

The parsers are deliberately forgiving: Instagram omits or renames fields frequently, so
every ``from_api`` tolerates missing keys and keeps the original payload in ``raw``
(excluded from ``repr`` and from default serialisation).
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "Collection",
    "Comment",
    "Media",
    "MediaResource",
    "MediaType",
    "Page",
    "User",
]

T = TypeVar("T")
BASE_URL = "https://www.instagram.com"


def _str_or_none(value: Any) -> str | None:
    return None if value is None or value == "" else str(value)


def _int_or_none(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _ts(value: Any) -> datetime | None:
    try:
        return None if value is None else datetime.fromtimestamp(int(value), tz=UTC)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


class _Model(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class User(_Model):
    """An Instagram account."""

    pk: str | None = None
    username: str
    full_name: str | None = None
    is_verified: bool = False
    is_private: bool | None = None
    profile_pic_url: str | None = None

    @property
    def url(self) -> str:
        """Profile URL."""
        return f"{BASE_URL}/{self.username}/"

    @classmethod
    def from_api(cls, data: dict[str, Any] | None) -> User:
        """Parse a web API ``user`` object (missing username becomes ``"unknown"``)."""
        d = data or {}
        return cls(
            pk=_str_or_none(d.get("pk") or d.get("pk_id") or d.get("id")),
            username=str(d.get("username") or "unknown"),
            full_name=_str_or_none(d.get("full_name")),
            is_verified=bool(d.get("is_verified", False)),
            is_private=d.get("is_private"),
            profile_pic_url=_str_or_none(d.get("profile_pic_url")),
        )


class MediaType(StrEnum):
    """Kind of post."""

    PHOTO = "photo"
    VIDEO = "video"
    CAROUSEL = "carousel"
    REEL = "reel"


class MediaResource(_Model):
    """One downloadable rendition (image candidate or video version)."""

    url: str
    width: int | None = None
    height: int | None = None
    kind: str = Field(default="image", description="image | video")

    @classmethod
    def from_api(cls, data: dict[str, Any], kind: str) -> MediaResource | None:
        """Parse ``{url,width,height}``; returns None if there is no URL."""
        url = data.get("url")
        if not url:
            return None
        return cls(url=url, width=_int_or_none(data.get("width")),
                   height=_int_or_none(data.get("height")), kind=kind)


def _resources(items: Any, kind: str) -> list[MediaResource]:
    if not isinstance(items, list):
        return []
    parsed = (MediaResource.from_api(i, kind) for i in items if isinstance(i, dict))
    res = [r for r in parsed if r is not None]
    return sorted(res, key=lambda r: (r.width or 0) * (r.height or 0), reverse=True)


def _media_type(d: dict[str, Any]) -> MediaType:
    if d.get("product_type") == "clips":
        return MediaType.REEL
    return {1: MediaType.PHOTO, 2: MediaType.VIDEO, 8: MediaType.CAROUSEL}.get(
        _int_or_none(d.get("media_type")) or 1, MediaType.PHOTO
    )


class Media(_Model):
    """A post, reel, video or carousel."""

    id: str
    pk: str | None = None
    code: str | None = Field(default=None, description="Shortcode used in post URLs")
    url: str | None = None
    media_type: MediaType = MediaType.PHOTO
    caption: str | None = None
    taken_at: datetime | None = None
    owner: User | None = None
    like_count: int | None = None
    comment_count: int | None = None
    play_count: int | None = None
    video_url: str | None = None
    video_duration: float | None = None
    thumbnail_url: str | None = None
    images: list[MediaResource] = Field(default_factory=list)
    videos: list[MediaResource] = Field(default_factory=list)
    children: list[Media] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict, repr=False, exclude=True)

    @property
    def shortcode(self) -> str | None:
        """Alias of :attr:`code`."""
        return self.code

    @property
    def is_video(self) -> bool:
        """True for videos and reels."""
        return self.media_type in (MediaType.VIDEO, MediaType.REEL)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Media:
        """Parse a web API v1 media item (feed/saved/clips/``media/{id}/info`` shape).

        Handles ``media_type`` 1/2/8, ``product_type == "clips"`` (reel), caption text,
        ``video_versions``, ``image_versions2.candidates``, ``carousel_media`` and
        ``play_count``/``ig_play_count``. Saved-feed wrappers ``{"media": {...}}`` are
        unwrapped.
        """
        d = data.get("media") if isinstance(data.get("media"), dict) else data
        assert isinstance(d, dict)
        mtype = _media_type(d)
        code = _str_or_none(d.get("code") or d.get("shortcode"))
        caption = d.get("caption")
        caption_text = caption.get("text") if isinstance(caption, dict) else caption
        images = _resources((d.get("image_versions2") or {}).get("candidates"), "image")
        videos = _resources(d.get("video_versions"), "video")
        children = [
            cls.from_api(c) for c in d.get("carousel_media") or [] if isinstance(c, dict)
        ]
        pk = _str_or_none(d.get("pk"))
        path = "reel" if mtype is MediaType.REEL else "p"
        duration = d.get("video_duration")
        return cls(
            id=str(d.get("id") or pk or code or ""),
            pk=pk,
            code=code,
            url=f"{BASE_URL}/{path}/{code}/" if code else None,
            media_type=mtype,
            caption=_str_or_none(caption_text),
            taken_at=_ts(d.get("taken_at")),
            owner=User.from_api(d["user"]) if isinstance(d.get("user"), dict) else None,
            like_count=_int_or_none(d.get("like_count")),
            comment_count=_int_or_none(d.get("comment_count")),
            play_count=_int_or_none(d.get("play_count") or d.get("ig_play_count")),
            video_url=videos[0].url if videos else None,
            video_duration=float(duration) if isinstance(duration, int | float) else None,
            thumbnail_url=images[0].url if images else (
                children[0].thumbnail_url if children else None
            ),
            images=images,
            videos=videos,
            children=children,
            raw=data,
        )


class Collection(_Model):
    """A saved-posts collection."""

    id: str
    name: str
    media_count: int | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Collection:
        """Parse a ``collections/list`` item."""
        return cls(
            id=str(data.get("collection_id") or data.get("id") or ""),
            name=str(data.get("collection_name") or data.get("name") or ""),
            media_count=_int_or_none(data.get("collection_media_count", data.get("media_count"))),
        )


class Comment(_Model):
    """A comment on a media item."""

    id: str
    text: str
    user: User | None = None
    created_at: datetime | None = None
    like_count: int | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Comment:
        """Parse a web API comment object."""
        return cls(
            id=str(data.get("pk") or data.get("id") or ""),
            text=str(data.get("text") or ""),
            user=User.from_api(data["user"]) if isinstance(data.get("user"), dict) else None,
            created_at=_ts(data.get("created_at")),
            like_count=_int_or_none(data.get("comment_like_count", data.get("like_count"))),
        )


class Page(_Model, Generic[T]):
    """One page of a cursor-paginated listing."""

    items: list[T] = Field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool = False
