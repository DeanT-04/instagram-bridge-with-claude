"""Eye tools: let Claude inspect Heliograph's own traces to diagnose failures."""

from __future__ import annotations

import time
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from heliograph import eye
from heliograph.mcp.common import LOCAL, clip, tool
from heliograph.mcp.runtime import Runtime

__all__ = ["compact_event", "register"]


def compact_event(ev: dict[str, Any], *, tracebacks: bool = False) -> dict[str, Any]:
    """Drop bulky/empty fields from a stored eye event."""
    out: dict[str, Any] = {k: ev.get(k) for k in ("ts", "kind", "name", "status", "duration_ms",
                                                   "trace_id", "span_id", "parent_id")}
    if ev.get("attrs"):
        out["attrs"] = ev["attrs"]
    err = ev.get("error")
    if err:
        out["error"] = {"type": err.get("type"), "message": clip(err.get("message"), 600)}
        if tracebacks and err.get("traceback"):
            out["error"]["traceback"] = err["traceback"][-3000:]
    if ev.get("artifacts"):
        out["artifacts"] = ev["artifacts"]
    return {k: v for k, v in out.items() if v is not None}


def register(server: FastMCP, rt: Runtime) -> None:
    """Register the eye tools."""

    @tool(server, annotations=LOCAL)
    async def eye_report(hours: float = 24, limit: int = 10) -> dict[str, Any]:
        """Health summary of Heliograph over the last `hours`: span count, error rate,
        per-operation latency (p50/p95/max) and failures, slowest spans, recent errors
        (with trace ids and screenshot/DOM artifacts) and anomalies such as repeated
        errors. Start here when a tool failed or feels slow."""
        rep = eye.report(window_seconds=hours * 3600, limit=max(1, min(limit, 50)))
        data = rep.model_dump(mode="json")
        data["ops"] = data["ops"][:25]
        return data

    @tool(server, annotations=LOCAL)
    async def eye_trace(trace_id: str, tracebacks: bool = True) -> dict[str, Any]:
        """Every event of one trace (one MCP tool call and everything it triggered:
        driver actions, HTTP calls, ffmpeg/whisper), oldest first, with errors,
        tracebacks and artifact paths. Trace ids appear in tool error messages and in
        eye_report/eye_recent."""
        events = eye.get_eye().query(trace_id=trace_id.strip(), limit=1000)
        events.reverse()
        return {"trace_id": trace_id, "count": len(events),
                "events": [compact_event(e, tracebacks=tracebacks) for e in events]}

    @tool(server, annotations=LOCAL)
    async def eye_recent(limit: int = 30, status: Literal["ok", "error"] | None = None,
                         name: str | None = None, minutes: float | None = None,
                         kind: Literal["span", "event", "uncaught", "artifact"] | None = None,
                         ) -> dict[str, Any]:
        """The most recent eye events, newest first. Filter by status ("error"), name
        (substring or SQL LIKE pattern, e.g. "mcp.%" or "cdp"), kind and a look-back in
        minutes."""
        pattern = None
        if name:
            pattern = name if "%" in name else f"%{name}%"
        since = time.time() - minutes * 60 if minutes else None
        events = eye.get_eye().query(since=since, status=status, name=pattern, kind=kind,
                                     limit=max(1, min(limit, 200)))
        return {"count": len(events), "events": [compact_event(e) for e in events]}
