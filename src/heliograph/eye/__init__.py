"""The background eye: local-first observability for Heliograph.

Typical use::

    from heliograph import eye

    with eye.span("cdp.navigate", url=url) as s:
        ...
        s.set(status_code=200)

    @eye.traced
    async def fetch_saved(cursor: str | None = None) -> Page[Media]: ...

Events go to ``<eye_dir>/events.jsonl`` (rotating) and ``<eye_dir>/eye.db`` (SQLite),
redacted first. See ``docs/ARCHITECTURE.md``.
"""

from heliograph.eye.context import ActiveSpan, current_span, current_trace_id
from heliograph.eye.core import Eye, configure, get_eye, reset
from heliograph.eye.events import Artifact, ErrorInfo, Event
from heliograph.eye.excepthook import install_excepthook
from heliograph.eye.live import tail
from heliograph.eye.redact import REDACTED, redact, redact_text
from heliograph.eye.reporting import EyeReport, report
from heliograph.eye.spans import attach_artifact, event, span, traced

__all__ = [
    "REDACTED",
    "ActiveSpan",
    "Artifact",
    "ErrorInfo",
    "Event",
    "Eye",
    "EyeReport",
    "attach_artifact",
    "configure",
    "current_span",
    "current_trace_id",
    "event",
    "get_eye",
    "install_excepthook",
    "redact",
    "redact_text",
    "report",
    "reset",
    "span",
    "tail",
    "traced",
]
