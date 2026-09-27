"""Start the real stdio server in a subprocess and do an MCP handshake (no Instagram)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from heliograph import __version__
from tests.mcp_server.test_tools import EXPECTED


async def test_stdio_initialize_and_list_tools(tmp_path: Path) -> None:
    env = {**os.environ, "HELIOGRAPH_HOME": str(tmp_path / "home"), "PYTHONUTF8": "1"}
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "heliograph.cli", "mcp"], env=env,
        cwd=str(tmp_path),
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        init = await session.initialize()
        assert init.serverInfo.name == "heliograph"
        assert init.serverInfo.version == __version__
        assert "confirm=true" in (init.instructions or "")
        tools = await session.list_tools()
        assert {t.name for t in tools.tools} == EXPECTED
        report = await session.call_tool("eye_report", {"hours": 1})
        assert not report.isError
    events = (tmp_path / "home" / "eye" / "events.jsonl").read_text(encoding="utf-8")
    assert "mcp.server.start" in events and "mcp.eye_report" in events
