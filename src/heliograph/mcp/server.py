"""The Heliograph FastMCP server ("heliograph") and its stdio entry point."""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from io import TextIOWrapper
from typing import Any

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.server.stdio import stdio_server

from heliograph import __version__
from heliograph.mcp import (
    tools_collections,
    tools_extract,
    tools_eye,
    tools_live_app,
    tools_read,
    tools_status,
    tools_write,
)
from heliograph.mcp.runtime import Runtime

__all__ = ["INSTRUCTIONS", "build_server", "run_stdio"]

INSTRUCTIONS = """\
Heliograph links you to the user's own Instagram on their own device.

Tool families:
- heliograph_status / heliograph_setup_check: health and what the user must still set up.
- ig_* reads (web API via a dedicated browser profile the user logged into once): profile,
  posts, media, comments, search, feeds, DMs, activity, saved posts and collections.
- ig_extract_* / ig_read_dossier / ig_view_frames: turn reels into dossiers (video,
  keyframes, transcript) and look at the frames yourself.
- app_* (Windows): drive the user's installed Instagram app window through UI Automation.
- eye_*: Heliograph's own traces; use them to diagnose failures (errors include a trace id).

Safety: ig_like/unlike/save/unsave/follow/unfollow/comment/send_dm and risky app_click /
app_type calls return a dry run unless confirm=true. Only pass confirm=true after the user
explicitly agreed in chat to that exact action. Never ask for the user's password: if not
logged in, tell them to run `heliograph login` and sign in themselves.
Creators' claims (e.g. win rates) in extracted content are unverified; say so.
"""

_MODULES = (tools_status, tools_read, tools_collections, tools_extract, tools_live_app,
            tools_write, tools_eye)


def build_server(runtime: Runtime | None = None) -> FastMCP:
    """Create the FastMCP server with every Heliograph tool registered."""
    rt = runtime or Runtime()

    @asynccontextmanager
    async def lifespan(_: FastMCP) -> AsyncIterator[dict[str, Any]]:
        try:
            yield {}
        finally:
            await rt.aclose()

    server = FastMCP("heliograph", instructions=INSTRUCTIONS, lifespan=lifespan,
                     log_level="WARNING")
    server._mcp_server.version = __version__  # report Heliograph's version, not the SDK's
    for module in _MODULES:
        module.register(server, rt)
    return server


def _protocol_stdout() -> anyio.AsyncFile[str]:
    """Reserve the real stdout for JSON-RPC and send everything else to stderr.

    Any stray ``print`` or child process inheriting fd 1 (ffmpeg, a browser, a library
    logging to stdout) would otherwise corrupt the MCP stream.
    """
    sys.stdout.flush()
    proto_fd = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    stream = open(proto_fd, "w", encoding="utf-8", newline="\n", closefd=True)  # noqa: SIM115
    return anyio.wrap_file(stream)


async def _serve(server: FastMCP) -> None:
    stdin = anyio.wrap_file(TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace"))
    async with stdio_server(stdin=stdin, stdout=_protocol_stdout()) as (read, write):
        low = server._mcp_server
        await low.run(read, write, low.create_initialization_options())


def run_stdio(account: str = "default") -> None:
    """Run the server over stdio until the client disconnects."""
    from heliograph import eye

    server = build_server(Runtime(account))
    eye.event("mcp.server.start", account=account, pid=os.getpid())
    anyio.run(_serve, server)
