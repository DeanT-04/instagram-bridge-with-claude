"""Optional export of eye spans to Langfuse.

Active only when both ``LANGFUSE_PUBLIC_KEY`` and ``LANGFUSE_SECRET_KEY`` are set *and*
the ``langfuse`` package is importable. ``langfuse`` is deliberately not a dependency.
Supports the v2 client API (``client.span``) and falls back to ``create_event`` (v3).
Export failures are swallowed: Langfuse must never affect Heliograph.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
from datetime import UTC, datetime
from typing import Any

from heliograph.eye.events import Event

__all__ = ["LangfuseSink", "langfuse_enabled", "maybe_langfuse_sink"]


def langfuse_enabled() -> bool:
    """True if Langfuse keys are present and the package can be imported."""
    if not (os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")):
        return False
    try:
        return importlib.util.find_spec("langfuse") is not None
    except (ImportError, ValueError):
        return False


class LangfuseSink:
    """Sink forwarding finished spans to a Langfuse client."""

    def __init__(self, client: Any) -> None:
        self.client = client

    def write(self, event: Event, line: str) -> None:
        """Export one event (already-redacted data is taken from ``line``)."""
        data = json.loads(line)
        try:
            start = datetime.fromtimestamp(event.ts_epoch, tz=UTC)
            end = datetime.fromtimestamp(
                event.ts_epoch + (event.duration_ms or 0) / 1000, tz=UTC
            )
            level = "ERROR" if event.status == "error" else "DEFAULT"
            status_message = (data.get("error") or {}).get("message")
            meta = {"attrs": data.get("attrs"), "artifacts": data.get("artifacts"),
                    "span_id": event.span_id, "parent_id": event.parent_id}
            if hasattr(self.client, "span"):  # langfuse v2
                self.client.span(
                    trace_id=event.trace_id, id=event.span_id,
                    parent_observation_id=event.parent_id, name=event.name,
                    start_time=start, end_time=end, metadata=meta, level=level,
                    status_message=status_message,
                )
            elif hasattr(self.client, "create_event"):  # langfuse v3+
                self.client.create_event(
                    name=event.name, metadata=meta, level=level, status_message=status_message,
                )
        except Exception:
            pass

    def close(self) -> None:
        """Flush pending exports."""
        try:
            self.client.flush()
        except Exception:
            pass


def maybe_langfuse_sink() -> LangfuseSink | None:
    """Return a :class:`LangfuseSink` if enabled, else ``None``. Never raises."""
    if not langfuse_enabled():
        return None
    try:
        module = importlib.import_module("langfuse")
        return LangfuseSink(module.Langfuse())
    except Exception:
        return None
