"""Record uncaught exceptions (main thread and worker threads) as ``uncaught`` events."""

from __future__ import annotations

import sys
import threading
import traceback
from types import TracebackType

from heliograph.eye.context import current_span
from heliograph.eye.core import get_eye
from heliograph.eye.events import ErrorInfo, Event, new_id

__all__ = ["install_excepthook", "record_uncaught"]

_installed = False


def record_uncaught(
    exc_type: type[BaseException],
    exc: BaseException,
    tb: TracebackType | None,
    *,
    where: str = "main",
) -> None:
    """Write an ``uncaught`` event for ``exc``. Never raises."""
    try:
        active = current_span()
        get_eye().emit(
            Event(
                kind="uncaught",
                name=f"uncaught.{where}",
                trace_id=active.trace_id if active else new_id(32),
                parent_id=active.span_id if active else None,
                status="error",
                error=ErrorInfo(
                    type=exc_type.__name__,
                    message=str(exc)[:2000],
                    traceback="".join(traceback.format_exception(exc_type, exc, tb))[-8000:],
                ),
            )
        )
    except Exception:  # pragma: no cover
        pass


def install_excepthook() -> None:
    """Chain into ``sys.excepthook`` and ``threading.excepthook``. Idempotent.

    ``KeyboardInterrupt`` is not recorded. The previous hooks still run afterwards.
    """
    global _installed
    if _installed:
        return
    prev_sys = sys.excepthook
    prev_thread = threading.excepthook

    def _sys_hook(
        exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None
    ) -> None:
        if not issubclass(exc_type, KeyboardInterrupt):
            record_uncaught(exc_type, exc, tb)
        prev_sys(exc_type, exc, tb)

    def _thread_hook(args: threading.ExceptHookArgs) -> None:
        if args.exc_value is not None and not issubclass(args.exc_type, KeyboardInterrupt):
            name = args.thread.name if args.thread else "thread"
            record_uncaught(args.exc_type, args.exc_value, args.exc_traceback, where=name)
        prev_thread(args)

    sys.excepthook = _sys_hook
    threading.excepthook = _thread_hook
    _installed = True
