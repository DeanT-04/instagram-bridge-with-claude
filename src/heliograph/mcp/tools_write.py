"""Confirm-gated write tools (web API through the dedicated browser profile).

Without ``confirm=true`` every tool returns a dry run describing what *would* happen and
touches nothing (no network). With it, the request goes through the write rate limiter.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from heliograph.instagram.actions import WriteActions
from heliograph.instagram.endpoints import pk_to_code
from heliograph.mcp.common import WRITE, clip, tool
from heliograph.mcp.runtime import Runtime

__all__ = ["register"]

_ASK = ("Tell the user exactly this and ask for explicit confirmation in chat. Only if they "
        "clearly say yes, call the same tool again with confirm=true.")


def _media_target(ref: str) -> dict[str, Any]:
    pk = WriteActions.media_pk(ref)  # validates locally, no network
    code = pk_to_code(pk)
    return {"media_pk": pk, "url": f"https://www.instagram.com/p/{code}/"}


def _dry(action: str, would: str, target: dict[str, Any]) -> dict[str, Any]:
    return {"performed": False, "dry_run": True, "action": action, "would": would,
            "target": target, "next_step": _ASK}


def _done(action: str, target: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    status = response.get("status") if isinstance(response, dict) else None
    return {"performed": True, "action": action, "target": target, "status": status or "ok"}


def register(server: FastMCP, rt: Runtime) -> None:
    """Register the write tools."""

    async def media_write(action: str, verb: str, media: str, confirm: bool,
                          **kwargs: Any) -> dict[str, Any]:
        target = _media_target(media)
        if not confirm:
            return _dry(action, f"{verb} the post {target['url']} on the user's account",
                        target)
        svc = await rt.service()
        resp = await getattr(svc, action)(media, confirm=True, **kwargs)
        return _done(action, target, resp)

    async def user_write(action: str, username: str, confirm: bool) -> dict[str, Any]:
        user = username.strip().lstrip("@")
        if not user:
            raise ValueError("username is empty")
        target = {"username": user, "url": f"https://www.instagram.com/{user}/"}
        if not confirm:
            return _dry(action, f"{action} @{user} from the user's account", target)
        svc = await rt.service()
        return _done(action, target, await getattr(svc, action)(user, confirm=True))

    @tool(server, annotations=WRITE)
    async def ig_like(media: str, confirm: bool = False) -> dict[str, Any]:
        """Like a post/reel (`media` = URL, shortcode or pk) as the user. WRITE ACTION:
        without confirm=true only a dry run is returned. Set confirm=true only after the
        user explicitly agreed in chat to this exact action."""
        return await media_write("like", "like", media, confirm)

    @tool(server, annotations=WRITE)
    async def ig_unlike(media: str, confirm: bool = False) -> dict[str, Any]:
        """Remove the user's like from a post/reel. WRITE ACTION: dry run unless
        confirm=true (only after explicit user agreement in chat)."""
        return await media_write("unlike", "remove the like from", media, confirm)

    @tool(server, annotations=WRITE)
    async def ig_save(media: str, collection: str | None = None,
                      confirm: bool = False) -> dict[str, Any]:
        """Save a post/reel, optionally into a collection (name or id). WRITE ACTION: dry
        run unless confirm=true (only after explicit user agreement in chat)."""
        target = _media_target(media)
        where = f" into collection {collection!r}" if collection else ""
        if not confirm:
            return _dry("save", f"save the post {target['url']}{where}",
                        {**target, "collection": collection})
        svc = await rt.service()
        cid = await svc.resolve_collection(collection) if collection else None
        resp = await svc.save(media, collection_id=cid, confirm=True)
        return _done("save", {**target, "collection_id": cid}, resp)

    @tool(server, annotations=WRITE)
    async def ig_unsave(media: str, collection: str | None = None,
                        confirm: bool = False) -> dict[str, Any]:
        """Un-save a post/reel, or with `collection` only remove it from that collection.
        WRITE ACTION: dry run unless confirm=true (only after explicit user agreement)."""
        target = _media_target(media)
        where = f" from collection {collection!r}" if collection else " from saved posts"
        if not confirm:
            return _dry("unsave", f"remove the post {target['url']}{where}",
                        {**target, "collection": collection})
        svc = await rt.service()
        cid = await svc.resolve_collection(collection) if collection else None
        resp = await svc.unsave(media, collection_id=cid, confirm=True)
        return _done("unsave", {**target, "collection_id": cid}, resp)

    @tool(server, annotations=WRITE)
    async def ig_follow(username: str, confirm: bool = False) -> dict[str, Any]:
        """Follow an account. WRITE ACTION: dry run unless confirm=true (only after
        explicit user agreement in chat)."""
        return await user_write("follow", username, confirm)

    @tool(server, annotations=WRITE)
    async def ig_unfollow(username: str, confirm: bool = False) -> dict[str, Any]:
        """Unfollow an account. WRITE ACTION: dry run unless confirm=true (only after
        explicit user agreement in chat)."""
        return await user_write("unfollow", username, confirm)

    @tool(server, annotations=WRITE)
    async def ig_comment(media: str, text: str, confirm: bool = False) -> dict[str, Any]:
        """Post a public comment on a post/reel as the user. WRITE ACTION: dry run unless
        confirm=true. Show the user the exact text first; set confirm=true only after
        they approved that exact text in chat."""
        if not text.strip():
            raise ValueError("comment text is empty")
        target = {**_media_target(media), "text": text}
        if not confirm:
            return _dry("comment", f"publicly comment {clip(text, 200)!r} on "
                                   f"{target['url']}", target)
        resp = await (await rt.service()).comment(media, text, confirm=True)
        return _done("comment", target, resp)

    @tool(server, annotations=WRITE)
    async def ig_send_dm(text: str, username: str | None = None, thread_id: str | None = None,
                         confirm: bool = False) -> dict[str, Any]:
        """Send a direct message as the user, to `username` or into an existing
        `thread_id` (exactly one). WRITE ACTION: dry run unless confirm=true. Show the
        recipient and the exact text first; confirm only after the user approved it."""
        if (username is None) == (thread_id is None):
            raise ValueError("pass exactly one of username or thread_id")
        if not text.strip():
            raise ValueError("message text is empty")
        user = username.strip().lstrip("@") if username else None
        target = {"username": user, "thread_id": thread_id, "text": text}
        if not confirm:
            to = f"@{user}" if user else f"thread {thread_id}"
            return _dry("send_dm", f"send the DM {clip(text, 200)!r} to {to}",
                        {k: v for k, v in target.items() if v is not None})
        resp = await (await rt.service()).send_dm(text, username=user, thread_id=thread_id,
                                                  confirm=True)
        return _done("send_dm", {k: v for k, v in target.items() if v is not None}, resp)
