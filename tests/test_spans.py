from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import pytest

from heliograph import eye
from heliograph.eye.core import Eye
from heliograph.eye.sinks import JsonlSink
from tests.conftest import Recorded


def test_sync_span_records_ok(recorded: Recorded) -> None:
    with eye.span("unit.ok", url="https://x") as s:
        s.set(extra=1)
    [ev] = recorded()
    assert ev["name"] == "unit.ok"
    assert ev["status"] == "ok"
    assert ev["duration_ms"] >= 0
    assert ev["attrs"] == {"url": "https://x", "extra": 1}
    assert ev["parent_id"] is None and len(ev["trace_id"]) == 32


def test_span_records_error_and_reraises(recorded: Recorded) -> None:
    with pytest.raises(ValueError, match="boom"), eye.span("unit.fail"):
        raise ValueError("boom")
    [ev] = recorded()
    assert ev["status"] == "error"
    assert ev["error"]["type"] == "ValueError"
    assert ev["error"]["message"] == "boom"
    assert "Traceback" in ev["error"]["traceback"]


def test_nested_spans_share_trace_and_link_parent(recorded: Recorded) -> None:
    with eye.span("outer") as outer, eye.span("inner") as inner:
        assert eye.current_span() is inner
        assert eye.current_trace_id() == outer.trace_id
    assert eye.current_span() is None
    evs = {e["name"]: e for e in recorded()}
    assert evs["inner"]["trace_id"] == evs["outer"]["trace_id"]
    assert evs["inner"]["parent_id"] == evs["outer"]["span_id"]


def test_sibling_top_level_spans_get_distinct_traces(recorded: Recorded) -> None:
    with eye.span("a"):
        pass
    with eye.span("b"):
        pass
    a, b = recorded()
    assert a["trace_id"] != b["trace_id"]


async def test_async_span_and_task_propagation(recorded: Recorded) -> None:
    async def child(i: int) -> None:
        async with eye.span("child", i=i):
            await asyncio.sleep(0)

    async with eye.span("parent") as parent:
        await asyncio.gather(child(1), child(2))
    evs = recorded()
    children = [e for e in evs if e["name"] == "child"]
    assert len(children) == 2
    assert all(c["parent_id"] == parent.span_id for c in children)


async def test_traced_async_and_sync(recorded: Recorded) -> None:
    @eye.traced
    async def fetch(url: str, password: str = "x") -> str:
        return url.upper()

    @eye.traced(name="custom.name")
    def add(a: int, b: int) -> int:
        return a + b

    assert await fetch("abc", password="hunter2") == "ABC"
    assert add(1, 2) == 3
    f, a = recorded()
    assert f["name"] == "test_spans.test_traced_async_and_sync.<locals>.fetch"
    assert f["attrs"]["args"] == {"url": "abc", "password": "[REDACTED]"}
    assert a["name"] == "custom.name" and a["attrs"]["args"] == {"a": 1, "b": 2}


def test_traced_error_and_method_skips_self(recorded: Recorded) -> None:
    class Driver:
        @eye.traced(name="drv.go")
        def go(self, n: int) -> None:
            raise RuntimeError(f"bad {n}")

    with pytest.raises(RuntimeError):
        Driver().go(5)
    [ev] = recorded()
    assert ev["status"] == "error" and ev["attrs"]["args"] == {"n": 5}


def test_traced_without_args_capture(recorded: Recorded) -> None:
    @eye.traced(capture_args=False)
    def f(x: int) -> int:
        return x

    f(1)
    assert recorded()[0]["attrs"] == {}


def test_long_args_are_truncated(recorded: Recorded) -> None:
    @eye.traced
    def f(text: str) -> None:
        return None

    f("a b " * 500)
    assert len(recorded()[0]["attrs"]["args"]["text"]) <= 201


def test_attach_artifact_and_on_error_hook(recorded: Recorded, tmp_path: Path) -> None:
    shot = tmp_path / "shot.png"

    def hook(active: eye.ActiveSpan, exc: BaseException) -> None:
        eye.attach_artifact(shot, "screenshot")

    with pytest.raises(KeyError), eye.span("ui.click", on_error=hook):
        raise KeyError("x")
    eye.attach_artifact(tmp_path / "loose.json", "snapshot")
    ev, loose = recorded()
    assert ev["artifacts"] == [{"path": str(shot), "kind": "screenshot"}]
    assert loose["kind"] == "artifact" and loose["artifacts"][0]["kind"] == "snapshot"


def test_secrets_are_redacted_in_jsonl(isolated_home: Path) -> None:
    with eye.span("http", headers={"Authorization": "Bearer abcdefghijklmnop"},
                  note="sessionid=12345%3Aabc"):
        pass
    text = (isolated_home / "eye" / "events.jsonl").read_text(encoding="utf-8")
    assert "abcdefghijklmnop" not in text and "12345%3Aabc" not in text
    assert json.loads(text.splitlines()[0])["attrs"]["headers"]["Authorization"] == "[REDACTED]"


def test_point_event_joins_current_trace(recorded: Recorded) -> None:
    with eye.span("outer") as outer:
        eye.event("rate_limit.wait", seconds=1.5)
    ev = recorded(kind="event")[0]
    assert ev["trace_id"] == outer.trace_id and ev["parent_id"] == outer.span_id


def test_jsonl_rotation(tmp_path: Path) -> None:
    sink = JsonlSink(tmp_path / "e.jsonl", max_bytes=200, backups=2)
    e = eye.Event(name="x", trace_id="t")
    for _ in range(20):
        sink.write(e, "x" * 60)
    assert (tmp_path / "e.jsonl").stat().st_size <= 200
    assert (tmp_path / "e.jsonl.1").exists() and (tmp_path / "e.jsonl.2").exists()
    assert not (tmp_path / "e.jsonl.3").exists()


def test_failing_sink_never_breaks_caller(tmp_path: Path, recorded: Recorded) -> None:
    class Broken:
        def write(self, event: eye.Event, line: str) -> None:
            raise OSError("disk full")

        def close(self) -> None:
            pass

    e = Eye(tmp_path / "eye2", extra_sinks=[Broken()])
    e.emit(eye.Event(name="x", trace_id="t"))
    assert e.query()[0]["name"] == "x"
    e.close()


def test_threads_do_not_inherit_span(recorded: Recorded) -> None:
    seen: list[object] = []
    with eye.span("main"):
        t = threading.Thread(target=lambda: seen.append(eye.current_span()))
        t.start()
        t.join()
    assert seen == [None]


def test_excepthook_records_uncaught(recorded: Recorded) -> None:
    from heliograph.eye.excepthook import record_uncaught

    try:
        raise ZeroDivisionError("nope")
    except ZeroDivisionError as exc:
        record_uncaught(type(exc), exc, exc.__traceback__)
    [ev] = recorded(kind="uncaught")
    assert ev["error"]["type"] == "ZeroDivisionError" and ev["status"] == "error"


def test_sqlite_query_filters(recorded: Recorded) -> None:
    for name in ("cdp.a", "cdp.b", "uia.c"):
        with eye.span(name):
            pass
    with pytest.raises(ValueError), eye.span("cdp.bad"):
        raise ValueError
    idx = eye.get_eye()
    assert {e["name"] for e in idx.query(name="cdp.%")} == {"cdp.a", "cdp.b", "cdp.bad"}
    assert [e["name"] for e in idx.query(status="error")] == ["cdp.bad"]
    tid = idx.query(name="uia.c")[0]["trace_id"]
    assert len(idx.query(trace_id=tid)) == 1
    assert idx.query(since=9e12) == []


def test_eye_files_are_owner_only_on_posix(tmp_path: Path) -> None:
    import os
    import stat

    from heliograph.eye.core import Eye

    e = Eye(tmp_path / "eye2")
    with eye.span("perm"):
        pass
    e.emit(eye.Event(name="x", trace_id="t"))
    if os.name == "posix":
        assert stat.S_IMODE((tmp_path / "eye2").stat().st_mode) == 0o700
        for name in ("events.jsonl", "eye.db"):
            assert stat.S_IMODE((tmp_path / "eye2" / name).stat().st_mode) == 0o600
    assert (tmp_path / "eye2" / "events.jsonl").exists()
    e.close()


def test_summarize_args_keeps_only_length_of_message_text() -> None:
    from heliograph.eye.spans import summarize_args

    def send_dm(text: str, username: str, confirm: bool = False) -> None: ...

    out = summarize_args(send_dm, ("meet me at 5, private", "alice"), {"confirm": True})
    assert out == {"text": "<21 chars>", "username": "alice", "confirm": True}
