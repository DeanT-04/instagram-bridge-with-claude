"""Health summaries computed from the eye's SQLite index.

:func:`report` is what ``heliograph eye report`` prints and what the MCP ``eye_report``
tool returns, so Claude can inspect recent failures itself.
"""

from __future__ import annotations

import time
from collections import Counter, defaultdict
from typing import Any

from pydantic import BaseModel, Field

from heliograph.eye.core import Eye, get_eye

__all__ = ["Anomaly", "ErrorSummary", "EyeReport", "OpStats", "percentile", "report"]


class OpStats(BaseModel):
    """Latency and failure statistics for one span name."""

    name: str
    count: int
    errors: int
    error_rate: float
    p50_ms: float | None
    p95_ms: float | None
    max_ms: float | None


class ErrorSummary(BaseModel):
    """A recent failed span."""

    ts: str
    name: str
    trace_id: str | None
    error_type: str | None
    message: str | None
    artifacts: list[dict[str, Any]] = Field(default_factory=list)


class Anomaly(BaseModel):
    """Something that looks wrong (repeated identical error, op failing most of the time)."""

    kind: str
    name: str
    detail: str
    count: int


class EyeReport(BaseModel):
    """Structured summary of recent activity."""

    window_seconds: float
    total: int
    errors: int
    error_rate: float
    ops: list[OpStats]
    slowest: list[dict[str, Any]]
    recent_errors: list[ErrorSummary]
    anomalies: list[Anomaly]


def percentile(values: list[float], pct: float) -> float | None:
    """Linear-interpolated percentile (``pct`` in 0..100) of ``values``; None if empty."""
    if not values:
        return None
    ordered = sorted(values)
    k = (len(ordered) - 1) * pct / 100
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    return round(ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo), 3)


def _anomalies(spans: list[dict[str, Any]], ops: list[OpStats]) -> list[Anomaly]:
    found: list[Anomaly] = []
    signatures: Counter[tuple[str, str, str]] = Counter()
    for ev in spans:
        err = ev.get("error")
        if ev.get("status") == "error" and err:
            signatures[(ev["name"], err.get("type", ""), (err.get("message") or "")[:120])] += 1
    for (name, etype, msg), n in signatures.most_common():
        if n >= 3:
            found.append(Anomaly(kind="repeated_error", name=name, detail=f"{etype}: {msg}",
                                 count=n))
    for op in ops:
        if op.count >= 4 and op.error_rate >= 0.5:
            found.append(Anomaly(kind="high_failure_rate", name=op.name,
                                 detail=f"{op.errors}/{op.count} failed", count=op.errors))
    return found


def report(
    *, window_seconds: float = 24 * 3600, limit: int = 10, eye: Eye | None = None
) -> EyeReport:
    """Summarise the last ``window_seconds`` of spans.

    Args:
        window_seconds: How far back to look.
        limit: Max entries in ``slowest`` and ``recent_errors``.
        eye: Eye to read from (defaults to the process-wide one).
    """
    eye = eye or get_eye()
    spans = eye.query(since=time.time() - window_seconds, kind="span", limit=50_000)
    durations: dict[str, list[float]] = defaultdict(list)
    errors_by_name: Counter[str] = Counter()
    for ev in spans:
        if ev.get("duration_ms") is not None:
            durations[ev["name"]].append(float(ev["duration_ms"]))
        if ev.get("status") == "error":
            errors_by_name[ev["name"]] += 1
    counts = Counter(ev["name"] for ev in spans)
    ops = sorted(
        (
            OpStats(
                name=name, count=n, errors=errors_by_name[name],
                error_rate=round(errors_by_name[name] / n, 4),
                p50_ms=percentile(durations[name], 50), p95_ms=percentile(durations[name], 95),
                max_ms=max(durations[name]) if durations[name] else None,
            )
            for name, n in counts.items()
        ),
        key=lambda o: (-o.errors, -(o.p95_ms or 0)),
    )
    slowest = [
        {"name": ev["name"], "ts": ev["ts"], "duration_ms": ev["duration_ms"],
         "status": ev["status"], "trace_id": ev.get("trace_id")}
        for ev in sorted(spans, key=lambda e: e.get("duration_ms") or 0, reverse=True)[:limit]
    ]
    recent_errors = [
        ErrorSummary(
            ts=ev["ts"], name=ev["name"], trace_id=ev.get("trace_id"),
            error_type=(ev.get("error") or {}).get("type"),
            message=(ev.get("error") or {}).get("message"),
            artifacts=ev.get("artifacts") or [],
        )
        for ev in spans if ev.get("status") == "error"
    ][:limit]
    total, errors = len(spans), sum(errors_by_name.values())
    return EyeReport(
        window_seconds=window_seconds, total=total, errors=errors,
        error_rate=round(errors / total, 4) if total else 0.0, ops=ops, slowest=slowest,
        recent_errors=recent_errors, anomalies=_anomalies(spans, ops),
    )
