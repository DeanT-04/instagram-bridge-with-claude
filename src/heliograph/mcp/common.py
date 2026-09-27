"""Shared plumbing for MCP tools: tracing, error mapping and compact serialisation.

Every tool is registered through :func:`tool`, which

* opens an eye span ``mcp.<tool name>`` (one trace per tool call, so everything the call
  does below — driver actions, HTTP requests, ffmpeg — shares its ``trace_id``);
* maps :class:`HeliographError` subclasses to a :class:`ToolError` whose text carries the
  error type, message, the ``hint`` and the trace id (for ``eye_trace``);
* serialises plain results to compact JSON text (images and text pass through).
"""

from __future__ import annotations

import functools
import json
import re
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, ParamSpec, TypeVar

import pydantic_core
from mcp.server.fastmcp import FastMCP, Image
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import (
    AudioContent,
    EmbeddedResource,
    ImageContent,
    ResourceLink,
    TextContent,
    ToolAnnotations,
)

from heliograph import eye
from heliograph.errors import HeliographError, RateLimitedError
from heliograph.eye.spans import summarize_args

__all__ = [
    "EXTRACT",
    "LOCAL",
    "READ_ONLY",
    "WRITE",
    "ToolError",
    "clip",
    "dump",
    "dump_media",
    "dump_page",
    "dump_user",
    "error_text",
    "tool",
]

P = ParamSpec("P")
R = TypeVar("R")

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True)
LOCAL = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
# Reads Instagram, writes only local dossier files.
EXTRACT = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True,
                          openWorldHint=True)

_SECRETISH = re.compile(r"(sessionid|csrftoken|ds_user_id)=[^;\s]+", re.IGNORECASE)


def clip(text: str | None, limit: int | None) -> str | None:
    """Trim ``text`` to ``limit`` characters (``None``/``<=0`` = no limit)."""
    if text is None or limit is None or limit <= 0 or len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def dump(value: Any) -> Any:
    """JSON-able form of models/dataclasses/paths/datetimes, dropping ``None`` fields."""
    if hasattr(value, "model_dump"):
        return _prune(value.model_dump(mode="json", exclude_none=True))
    return _prune(pydantic_core.to_jsonable_python(value, fallback=str))


def _prune(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _prune(v) for k, v in value.items() if v is not None and k != "raw"}
    if isinstance(value, list):
        return [_prune(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def dump_user(user: Any) -> dict[str, Any] | None:
    """Compact user: username, full name, pk, verified/private flags."""
    if user is None:
        return None
    out = {"username": user.username, "full_name": user.full_name, "pk": user.pk}
    if user.is_verified:
        out["is_verified"] = True
    if user.is_private:
        out["is_private"] = True
    return {k: v for k, v in out.items() if v is not None}


def dump_media(
    media: Any, *, caption_chars: int | None = 300, urls: bool = False
) -> dict[str, Any]:
    """Compact media dict (no raw payload, no CDN rendition lists unless ``urls``)."""
    out: dict[str, Any] = {
        "code": media.code,
        "pk": media.pk,
        "url": media.url,
        "type": media.media_type.value,
        "owner": dump_user(media.owner),
        "caption": clip(media.caption, caption_chars),
        "taken_at": media.taken_at.isoformat() if media.taken_at else None,
        "likes": media.like_count,
        "comments": media.comment_count,
        "plays": media.play_count,
        "duration_s": media.video_duration,
    }
    if media.children:
        out["carousel_items"] = len(media.children)
    if urls:
        out["video_url"] = media.video_url
        out["thumbnail_url"] = media.thumbnail_url
    return {k: v for k, v in out.items() if v is not None}


def dump_page(
    page: Any, item: Callable[[Any], Any], *, truncated: int = 0
) -> dict[str, Any]:
    """``{items, next_cursor, has_more}`` for a :class:`Page`."""
    out: dict[str, Any] = {
        "count": len(page.items),
        "items": [item(i) for i in page.items],
        "has_more": page.has_more,
        "next_cursor": page.next_cursor,
    }
    if truncated:
        out["skipped_on_last_page"] = truncated
    return out


def error_text(exc: BaseException, trace_id: str | None) -> str:
    """User-facing message for a failed tool call."""
    if isinstance(exc, HeliographError):
        text = f"{type(exc).__name__}: {exc.message or type(exc).__doc__ or ''}".rstrip()
        if isinstance(exc, RateLimitedError) and exc.retry_after:
            text += f" (retry after ~{exc.retry_after:.0f}s)"
        if exc.hint:
            text += f"\nHint: {exc.hint}"
    elif isinstance(exc, ValueError | TypeError):
        text = f"Invalid request: {exc}"
    else:
        text = f"Unexpected {type(exc).__name__}: {exc}"
    text = _SECRETISH.sub(r"\1=[redacted]", text)
    if trace_id:
        text += f"\nTrace: {trace_id} (call eye_trace with this id for details)"
    return text


def _render(result: Any) -> Any:
    if isinstance(result, str | Image) or _is_content(result):
        return result
    if isinstance(result, list | tuple) and any(
        isinstance(r, Image) or _is_content(r) for r in result
    ):
        return [r if isinstance(r, str | Image) or _is_content(r) else _json(r) for r in result]
    return _json(result)


_CONTENT = (TextContent, ImageContent, AudioContent, ResourceLink, EmbeddedResource)


def _is_content(value: Any) -> bool:
    return isinstance(value, _CONTENT)


def _json(value: Any) -> str:
    return json.dumps(dump(value), ensure_ascii=False, separators=(",", ":"), default=str)


def tool(
    server: FastMCP,
    *,
    annotations: ToolAnnotations | None = None,
    name: str | None = None,
) -> Callable[[Callable[P, Awaitable[Any]]], Callable[P, Awaitable[Any]]]:
    """Register an async function as a traced, error-mapped MCP tool.

    The function's docstring is the tool description Claude reads.
    """

    def decorate(fn: Callable[P, Awaitable[Any]]) -> Callable[P, Awaitable[Any]]:
        tool_name = name or fn.__name__

        @functools.wraps(fn)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
            attrs = summarize_args(fn, args, kwargs)
            async with eye.span(f"mcp.{tool_name}", args=attrs) as s:
                try:
                    result = await fn(*args, **kwargs)
                except ToolError:
                    raise
                except Exception as exc:
                    s.set(error_type=type(exc).__name__)
                    raise ToolError(error_text(exc, s.trace_id)) from exc
                return _render(result)

        server.add_tool(
            wrapper,
            name=tool_name,
            description=(fn.__doc__ or "").strip(),
            annotations=annotations,
            structured_output=False,
        )
        return fn

    return decorate


def content_types(result: Sequence[Any]) -> list[str]:
    """Types of content blocks (test helper)."""
    return [getattr(r, "type", type(r).__name__) for r in result]
