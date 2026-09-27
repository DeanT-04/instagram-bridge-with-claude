"""Live, coloured tail of ``events.jsonl`` with a rolling health footer (``heliograph eye``)."""

from __future__ import annotations

import json
import time
from collections import Counter, deque
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from rich.console import Console, Group
from rich.live import Live
from rich.table import Table
from rich.text import Text

from heliograph.eye.reporting import percentile

__all__ = ["follow_lines", "health_line", "read_last", "render", "tail"]

_STATUS_STYLE = {"ok": "green", "error": "bold red", "running": "yellow"}


def read_last(path: Path, n: int) -> list[dict[str, Any]]:
    """Return the last ``n`` parseable events from a JSONL file (oldest first)."""
    if not path.exists():
        return []
    buf: deque[dict[str, Any]] = deque(maxlen=n)
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                buf.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return list(buf)


def follow_lines(path: Path, *, poll: float = 0.5) -> Iterator[dict[str, Any] | None]:
    """Yield new events appended to ``path`` forever; yields ``None`` on idle polls.

    Handles rotation (file replaced or truncated) by reopening from the start.
    """
    pos = path.stat().st_size if path.exists() else 0
    while True:
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            size = 0
        if size < pos:  # rotated
            pos = 0
        if size > pos:
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                fh.seek(pos)
                for line in fh:
                    if not line.endswith("\n"):
                        break
                    pos += len(line.encode("utf-8"))
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue
        else:
            yield None
            time.sleep(poll)


def health_line(events: Iterable[dict[str, Any]]) -> Text:
    """One-line rolling health: error rate, p95 latency, top failing ops."""
    spans = [e for e in events if e.get("kind") == "span"]
    if not spans:
        return Text("no spans yet", style="dim")
    errors = [e for e in spans if e.get("status") == "error"]
    rate = len(errors) / len(spans)
    p95 = percentile([float(e["duration_ms"]) for e in spans if e.get("duration_ms")], 95)
    top = Counter(e["name"] for e in errors).most_common(3)
    text = Text()
    text.append(f"spans {len(spans)}  ")
    text.append(f"errors {rate:.0%}  ", style="red" if rate > 0.1 else "green")
    text.append(f"p95 {p95 or 0:.0f} ms  ")
    if top:
        text.append("top failing: " + ", ".join(f"{n}×{c}" for n, c in top), style="red")
    return text


def render(events: list[dict[str, Any]], *, rows: int = 25) -> Group:
    """Build the renderable (event table + health line) for the last ``rows`` events."""
    table = Table(expand=True, show_edge=False, pad_edge=False)
    table.add_column("time", style="dim", no_wrap=True)
    table.add_column("trace", style="dim", no_wrap=True)
    table.add_column("name", style="cyan")
    table.add_column("status", no_wrap=True)
    table.add_column("ms", justify="right", no_wrap=True)
    table.add_column("detail", overflow="fold")
    for ev in events[-rows:]:
        status = ev.get("status", "?")
        err = ev.get("error") or {}
        detail = f"{err.get('type')}: {err.get('message')}" if err else ""
        if ev.get("artifacts"):
            detail += f"  [{len(ev['artifacts'])} artifact(s)]"
        dur = ev.get("duration_ms")
        indent = "  " if ev.get("parent_id") else ""
        table.add_row(
            str(ev.get("ts", ""))[11:23],
            str(ev.get("trace_id") or "")[:8],
            indent + str(ev.get("name", "")),
            Text(status, style=_STATUS_STYLE.get(status, "")),
            f"{dur:.0f}" if isinstance(dur, int | float) else "",
            detail,
        )
    return Group(table, health_line(events))


def tail(
    path: Path | None = None,
    *,
    follow: bool = True,
    lines: int = 25,
    console: Console | None = None,
    poll: float = 0.5,
) -> None:
    """Show recent events; with ``follow`` keep updating until Ctrl+C."""
    if path is None:
        from heliograph.config import get_settings

        path = get_settings().eye_path / "events.jsonl"
    console = console or Console()
    window: deque[dict[str, Any]] = deque(read_last(path, 500), maxlen=500)
    if not follow:
        console.print(render(list(window), rows=lines))
        return
    try:
        with Live(render(list(window), rows=lines), console=console, refresh_per_second=4) as live:
            for ev in follow_lines(path, poll=poll):
                if ev is not None:
                    window.append(ev)
                    live.update(render(list(window), rows=lines))
    except KeyboardInterrupt:
        pass
