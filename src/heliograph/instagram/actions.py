"""Write operations (like, save, follow, comment, DM) for :class:`InstagramService`.

Every method requires ``confirm=True`` and raises :class:`UnsafeActionError` otherwise,
before any network traffic. Requests go through the client's *write* rate limiter.

These endpoints follow the best-known Instagram web routes and are **not live-verified**
(Heliograph's test-suite never performs writes on a real account); only the request
construction is unit-tested.
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any

from heliograph.errors import UnsafeActionError
from heliograph.eye import traced
from heliograph.instagram import endpoints as ep

if TYPE_CHECKING:
    from heliograph.instagram.models import User
    from heliograph.instagram.service import ApiClient

__all__ = ["WriteActions"]


def _require(confirm: bool, action: str) -> None:
    if confirm is not True:
        raise UnsafeActionError(f"Refusing to {action} without confirm=True")


class WriteActions:
    """Mixin providing write operations; expects ``self.api`` and ``self.get_user``."""

    api: ApiClient

    async def get_user(self, username: str) -> User:  # provided by InstagramService
        raise NotImplementedError

    @staticmethod
    def media_pk(ref: str | int) -> str:
        """Numeric pk from a pk, ``pk_owner`` media id, shortcode or post/reel URL."""
        text = str(ref).strip()
        head = text.split("_")[0]
        if head.isdigit():
            return head
        return ep.code_to_pk(ep.shortcode_from_url(text))

    async def user_pk(self, user: str | int) -> str:
        """Numeric pk for a username or pk."""
        text = str(user).strip().lstrip("@")
        if text.isdigit():
            return text
        pk = (await self.get_user(text)).pk
        if not pk:
            raise UnsafeActionError(f"Could not resolve user {text!r}")
        return pk

    @traced(name="ig.like")
    async def like(self, media: str, *, confirm: bool = False) -> dict[str, Any]:
        """Like a post/reel."""
        _require(confirm, "like")
        return await self.api.post(ep.like(self.media_pk(media)))

    @traced(name="ig.unlike")
    async def unlike(self, media: str, *, confirm: bool = False) -> dict[str, Any]:
        """Remove a like."""
        _require(confirm, "unlike")
        return await self.api.post(ep.unlike(self.media_pk(media)))

    @traced(name="ig.save")
    async def save(self, media: str, *, collection_id: str | None = None,
                   confirm: bool = False) -> dict[str, Any]:
        """Save a post, optionally into a collection (numeric id)."""
        _require(confirm, "save")
        data = {"added_collection_ids": json.dumps([collection_id])} if collection_id else None
        return await self.api.post(ep.save(self.media_pk(media)), data)

    @traced(name="ig.unsave")
    async def unsave(self, media: str, *, collection_id: str | None = None,
                     confirm: bool = False) -> dict[str, Any]:
        """Unsave a post (or only remove it from ``collection_id``)."""
        _require(confirm, "unsave")
        data = {"removed_collection_ids": json.dumps([collection_id])} if collection_id else None
        return await self.api.post(ep.unsave(self.media_pk(media)), data)

    @traced(name="ig.follow")
    async def follow(self, user: str, *, confirm: bool = False) -> dict[str, Any]:
        """Follow a user (username or pk)."""
        _require(confirm, "follow")
        pk = await self.user_pk(user)
        return await self.api.post(ep.follow(pk), {"container_module": "profile",
                                                   "user_id": pk})

    @traced(name="ig.unfollow")
    async def unfollow(self, user: str, *, confirm: bool = False) -> dict[str, Any]:
        """Unfollow a user (username or pk)."""
        _require(confirm, "unfollow")
        pk = await self.user_pk(user)
        return await self.api.post(ep.unfollow(pk), {"container_module": "profile",
                                                     "user_id": pk})

    @traced(name="ig.comment")
    async def comment(self, media: str, text: str, *, confirm: bool = False) -> dict[str, Any]:
        """Post a comment on a media."""
        _require(confirm, "comment")
        if not text.strip():
            raise ValueError("Comment text is empty")
        return await self.api.post(ep.add_comment(self.media_pk(media)), {"comment_text": text})

    @traced(name="ig.send_dm")
    async def send_dm(
        self,
        text: str,
        *,
        thread_id: str | None = None,
        username: str | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Send a text DM into ``thread_id`` or to ``username`` (exactly one)."""
        _require(confirm, "send a direct message")
        if (thread_id is None) == (username is None):
            raise ValueError("Pass exactly one of thread_id or username")
        if not text.strip():
            raise ValueError("Message text is empty")
        token = str(uuid.uuid4())
        data: dict[str, Any] = {"action": "send_item", "text": text,
                                "client_context": token, "mutation_token": token}
        if thread_id is not None:
            data["thread_ids"] = json.dumps([thread_id])
        else:
            assert username is not None
            data["recipient_users"] = json.dumps([[await self.user_pk(username)]])
        return await self.api.post(ep.DIRECT_BROADCAST_TEXT, data)
