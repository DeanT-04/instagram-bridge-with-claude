from __future__ import annotations

import pytest

from heliograph import eye
from heliograph.eye.reporting import percentile


def test_percentile() -> None:
    assert percentile([], 50) is None
    assert percentile([5.0], 95) == 5.0
    assert percentile([1, 2, 3, 4], 50) == 2.5
    assert percentile(list(range(1, 101)), 95) == pytest.approx(95.05)


def _fail(name: str, msg: str = "Timeout waiting for selector") -> None:
    with pytest.raises(TimeoutError), eye.span(name):
        raise TimeoutError(msg)


def test_report_summarises_errors_latency_and_anomalies() -> None:
    for _ in range(5):
        with eye.span("cdp.saved"):
            pass
    for _ in range(4):
        _fail("uia.click")
    report = eye.report()
    assert report.total == 9 and report.errors == 4
    assert report.error_rate == pytest.approx(4 / 9, abs=1e-3)
    ops = {o.name: o for o in report.ops}
    assert ops["uia.click"].errors == 4 and ops["uia.click"].error_rate == 1.0
    assert ops["cdp.saved"].p50_ms is not None and ops["cdp.saved"].p95_ms is not None
    assert report.ops[0].name == "uia.click"  # failing ops first
    kinds = {(a.kind, a.name) for a in report.anomalies}
    assert ("repeated_error", "uia.click") in kinds
    assert ("high_failure_rate", "uia.click") in kinds
    assert len(report.recent_errors) == 4
    assert report.recent_errors[0].error_type == "TimeoutError"
    assert len(report.slowest) <= 10


def test_report_empty_and_window() -> None:
    r = eye.report()
    assert r.total == 0 and r.error_rate == 0.0 and r.anomalies == []
    with eye.span("x"):
        pass
    assert eye.report(window_seconds=-10).total == 0


def test_report_is_json_serialisable() -> None:
    _fail("a")
    assert '"recent_errors"' in eye.report().model_dump_json()


def test_distinct_errors_are_not_flagged_as_repeated() -> None:
    for i in range(3):
        _fail("net.get", f"error {i}")
    assert not [a for a in eye.report().anomalies if a.kind == "repeated_error"]
