"""Trace/span propagation via :mod:`contextvars` (works across threads and asyncio tasks)."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from heliograph.eye.events import Artifact

__all__ = ["ActiveSpan", "current_span", "current_trace_id"]


@dataclass
class ActiveSpan:
    """Mutable state of a span that is currently open."""

    name: str
    trace_id: str
    span_id: str
    parent_id: str | None
    attrs: dict[str, object] = field(default_factory=dict)
    artifacts: list[Artifact] = field(default_factory=list)

    def set(self, **attrs: object) -> None:
        """Add or overwrite attributes recorded with this span."""
        self.attrs.update(attrs)


_current: ContextVar[ActiveSpan | None] = ContextVar("heliograph_eye_span", default=None)


def current_span() -> ActiveSpan | None:
    """The innermost open span in this context, if any."""
    return _current.get()


def current_trace_id() -> str | None:
    """The trace id of the innermost open span, if any."""
    span = _current.get()
    return span.trace_id if span else None
