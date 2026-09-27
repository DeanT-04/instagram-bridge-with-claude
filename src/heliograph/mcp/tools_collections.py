"""Saved posts and saved-post collections."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from heliograph.mcp.common import READ_ONLY, dump_media, dump_page, tool
from heliograph.mcp.runtime import Runtime
from heliograph.mcp.tools_read import collect

__all__ = ["register"]


def register(server: FastMCP, rt: Runtime) -> None:
    """Register the collection tools."""

    @tool(server, annotations=READ_ONLY)
    async def ig_list_collections() -> dict[str, Any]:
        """List the user's saved-post collections (id, name, item count when known).

        Opens the user's Saved page in the Heliograph browser window to read them."""
        cols = await (await rt.service()).list_collections()
        return {"count": len(cols),
                "collections": [c.model_dump(exclude_none=True) for c in cols]}

    @tool(server, annotations=READ_ONLY)
    async def ig_collection_posts(collection: str, limit: int = 24, cursor: str | None = None,
                                  caption_chars: int = 300) -> dict[str, Any]:
        """Posts/reels in one saved collection. `collection` is its numeric id or its name
        (case-insensitive, e.g. "Trading strats"). Returns compact items with shortcodes you
        can pass to ig_extract_media; paginate with next_cursor."""
        svc = await rt.service()
        cid = await svc.resolve_collection(collection)
        page, dropped = await collect(lambda c: svc.collection_medias(cid, c), cursor, limit)
        out = dump_page(page, lambda m: dump_media(m, caption_chars=caption_chars),
                        truncated=dropped)
        return {"collection_id": cid, **out}

    @tool(server, annotations=READ_ONLY)
    async def ig_saved_posts(limit: int = 24, cursor: str | None = None,
                             caption_chars: int = 300) -> dict[str, Any]:
        """All saved posts (the "All posts" view, every collection combined), newest saved
        first. Paginate with next_cursor."""
        svc = await rt.service()
        page, dropped = await collect(svc.saved_medias, cursor, limit)
        return dump_page(page, lambda m: dump_media(m, caption_chars=caption_chars),
                         truncated=dropped)
