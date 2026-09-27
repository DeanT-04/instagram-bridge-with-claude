"""Heliograph's MCP server: the tools Claude uses to see and drive Instagram.

``build_server()`` returns the FastMCP instance (tests call its tools in-process);
``run_stdio()`` is what ``heliograph mcp`` runs for Claude Code.
Modules: ``runtime`` (lazy drivers), ``common`` (tracing, error mapping, serialisation),
``dossiers`` and one ``tools_*`` module per tool family.
"""

from heliograph.mcp.runtime import Runtime
from heliograph.mcp.server import INSTRUCTIONS, build_server, run_stdio

__all__ = ["INSTRUCTIONS", "Runtime", "build_server", "run_stdio"]
