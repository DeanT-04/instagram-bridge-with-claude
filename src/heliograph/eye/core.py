"""The :class:`Eye` recorder: owns the sinks and fans events out to them.

A single process-wide instance is created lazily by :func:`get_eye` from
:func:`heliograph.config.get_settings`. Tests (or embedders) can call :func:`configure`
to point it elsewhere. Recording never raises into application code.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from heliograph.eye.events import Event, to_json_line
from heliograph.eye.sinks import JsonlSink, Sink, SqliteSink

__all__ = ["Eye", "configure", "get_eye", "reset"]


class Eye:
    """Fan-out recorder for eye events.

    Args:
        directory: Folder holding ``events.jsonl`` (+ rotations), ``eye.db`` and
            ``artifacts/``.
        max_bytes: JSONL rotation threshold.
        backups: Number of rotated JSONL files to keep.
        extra_sinks: Additional sinks (e.g. Langfuse) to receive every event.
    """

    def __init__(
        self,
        directory: Path,
        *,
        max_bytes: int = 10 * 1024 * 1024,
        backups: int = 5,
        extra_sinks: Sequence[Sink] | None = None,
    ) -> None:
        from heliograph.config import ensure_private_dir

        self.directory = ensure_private_dir(directory)
        self.jsonl = JsonlSink(directory / "events.jsonl", max_bytes=max_bytes, backups=backups)
        self.index = SqliteSink(directory / "eye.db")
        self.sinks: list[Sink] = [self.jsonl, self.index, *(extra_sinks or [])]
        self.enabled = True

    @property
    def artifacts_dir(self) -> Path:
        """Folder where failure screenshots/snapshots should be stored."""
        return self.directory / "artifacts"

    def emit(self, event: Event) -> None:
        """Redact, serialise and write ``event`` to every sink. Never raises."""
        if not self.enabled:
            return
        try:
            line = to_json_line(event)
        except Exception as exc:  # pragma: no cover - defensive
            print(f"[heliograph.eye] could not serialise event: {exc!r}", file=sys.stderr)
            return
        for sink in self.sinks:
            try:
                sink.write(event, line)
            except Exception as exc:
                print(f"[heliograph.eye] sink {type(sink).__name__} failed: {exc!r}",
                      file=sys.stderr)

    def query(self, **filters: Any) -> list[dict[str, Any]]:
        """Query the SQLite index; see :meth:`SqliteSink.query`."""
        return self.index.query(**filters)

    def close(self) -> None:
        """Close all sinks."""
        for sink in self.sinks:
            try:
                sink.close()
            except Exception:  # pragma: no cover
                pass


_lock = threading.Lock()
_eye: Eye | None = None


def _build_default() -> Eye:
    from heliograph.config import get_settings
    from heliograph.eye.langfuse_sink import maybe_langfuse_sink

    settings = get_settings()
    extra = [s for s in (maybe_langfuse_sink(),) if s is not None]
    return Eye(
        settings.eye_path,
        max_bytes=settings.eye_max_bytes,
        backups=settings.eye_backups,
        extra_sinks=extra,
    )


def get_eye() -> Eye:
    """Return the process-wide :class:`Eye`, creating it from settings on first use."""
    global _eye
    if _eye is None:
        with _lock:
            if _eye is None:
                _eye = _build_default()
    return _eye


def configure(directory: Path, **kwargs: Any) -> Eye:
    """Replace the process-wide eye with one writing to ``directory``."""
    global _eye
    with _lock:
        if _eye is not None:
            _eye.close()
        _eye = Eye(directory, **kwargs)
    return _eye


def reset() -> None:
    """Close and forget the process-wide eye (it is rebuilt lazily on next use)."""
    global _eye
    with _lock:
        if _eye is not None:
            _eye.close()
        _eye = None
