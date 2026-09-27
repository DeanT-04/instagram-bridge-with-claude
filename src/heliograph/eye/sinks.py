"""Destinations for eye events: a rotating JSONL file and an SQLite index.

Sinks must never raise into application code; :class:`heliograph.eye.core.Eye` guards
every call, but sinks also keep their own failures contained.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from heliograph.eye.events import Event

__all__ = ["JsonlSink", "Sink", "SqliteSink"]


@runtime_checkable
class Sink(Protocol):
    """Anything that can persist events."""

    def write(self, event: Event, line: str) -> None:
        """Persist one event. ``line`` is its redacted JSON serialisation."""

    def close(self) -> None:
        """Flush and release resources."""


class JsonlSink:
    """Append-only JSON-lines log with size-based rotation (``events.jsonl.1`` ...)."""

    def __init__(self, path: Path, *, max_bytes: int = 10 * 1024 * 1024, backups: int = 5):
        self.path = path
        self.max_bytes = max_bytes
        self.backups = backups
        self._lock = threading.Lock()

    def _rotate(self) -> None:
        if self.backups <= 0:
            self.path.unlink(missing_ok=True)
            return
        for i in range(self.backups - 1, 0, -1):
            src = self.path.with_name(f"{self.path.name}.{i}")
            if src.exists():
                src.replace(self.path.with_name(f"{self.path.name}.{i + 1}"))
        self.path.replace(self.path.with_name(f"{self.path.name}.1"))

    def write(self, event: Event, line: str) -> None:
        """Append ``line``, rotating first if the file would exceed ``max_bytes``."""
        data = (line + "\n").encode("utf-8")
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                size = self.path.stat().st_size
            except FileNotFoundError:
                size = 0
            if size and size + len(data) > self.max_bytes:
                self._rotate()
            with self.path.open("ab") as fh:
                fh.write(data)

    def close(self) -> None:
        """Nothing to release (files are opened per write)."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    ts_epoch REAL NOT NULL,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    trace_id TEXT,
    span_id TEXT,
    parent_id TEXT,
    duration_ms REAL,
    error_type TEXT,
    error_message TEXT,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_events_ts ON events(ts_epoch);
CREATE INDEX IF NOT EXISTS ix_events_status ON events(status, ts_epoch);
CREATE INDEX IF NOT EXISTS ix_events_name ON events(name, ts_epoch);
CREATE INDEX IF NOT EXISTS ix_events_trace ON events(trace_id);
"""


class SqliteSink:
    """SQLite index of events, queryable by time, status, name and trace id."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=5)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def write(self, event: Event, line: str) -> None:
        """Insert one event row (the redacted JSON goes in ``data``)."""
        data = json.loads(line)
        err = data.get("error") or {}
        row = (
            data["ts"], data["ts_epoch"], data["kind"], data["name"], data["status"],
            data.get("trace_id"), data.get("span_id"), data.get("parent_id"),
            data.get("duration_ms"), err.get("type"), err.get("message"), line,
        )
        with self._lock:
            self._conn.execute(
                "INSERT INTO events (ts, ts_epoch, kind, name, status, trace_id, span_id,"
                " parent_id, duration_ms, error_type, error_message, data)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                row,
            )
            self._conn.commit()

    def query(
        self,
        *,
        since: float | None = None,
        until: float | None = None,
        status: str | None = None,
        name: str | None = None,
        trace_id: str | None = None,
        kind: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Return matching events (newest first) as dicts parsed from the stored JSON.

        ``name`` supports SQL ``LIKE`` wildcards (``%``).
        """
        clauses: list[str] = []
        params: list[Any] = []
        for col, op, val in (
            ("ts_epoch", ">=", since), ("ts_epoch", "<=", until), ("status", "=", status),
            ("trace_id", "=", trace_id), ("kind", "=", kind),
        ):
            if val is not None:
                clauses.append(f"{col} {op} ?")
                params.append(val)
        if name is not None:
            clauses.append("name LIKE ?")
            params.append(name)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT data FROM events {where} ORDER BY ts_epoch DESC, id DESC LIMIT ?"
        with self._lock:
            rows = self._conn.execute(sql, (*params, limit)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            self._conn.close()

