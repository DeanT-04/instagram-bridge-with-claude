"""The event record written by the eye, and helpers to (de)serialise it."""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from heliograph.eye.redact import redact, redact_text

__all__ = ["Artifact", "ErrorInfo", "Event", "EventKind", "Status", "new_id", "to_json_line"]

Status = Literal["ok", "error", "running"]
EventKind = Literal["span", "event", "uncaught", "artifact"]


def new_id(n: int = 16) -> str:
    """Return a random hex identifier of ``n`` characters."""
    return uuid.uuid4().hex[:n]


def utcnow_iso(ts: float | None = None) -> str:
    """ISO-8601 UTC timestamp with millisecond precision."""
    dt = datetime.fromtimestamp(time.time() if ts is None else ts, tz=UTC)
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class ErrorInfo(BaseModel):
    """Details of an exception captured by a span."""

    type: str
    message: str
    traceback: str | None = None


class Artifact(BaseModel):
    """A file (screenshot, DOM/UIA snapshot, HAR...) linked to a span."""

    path: str
    kind: str


class Event(BaseModel):
    """One observability record. Spans are written once, when they finish."""

    kind: EventKind = "span"
    name: str
    ts: str = Field(default_factory=utcnow_iso, description="Start time, ISO-8601 UTC")
    ts_epoch: float = Field(default_factory=time.time)
    trace_id: str
    span_id: str = Field(default_factory=new_id)
    parent_id: str | None = None
    status: Status = "ok"
    duration_ms: float | None = None
    attrs: dict[str, Any] = Field(default_factory=dict)
    error: ErrorInfo | None = None
    artifacts: list[Artifact] = Field(default_factory=list)


def _fallback(obj: object) -> str:
    return redact_text(repr(obj))[:500]


def to_json_line(event: Event) -> str:
    """Serialise ``event`` as a single redacted JSON line (no trailing newline)."""
    data = redact(event.model_dump(mode="python"))
    return json.dumps(data, default=_fallback, ensure_ascii=False, separators=(",", ":"))
