from __future__ import annotations

import io
from pathlib import Path

import pytest
from rich.console import Console

from heliograph import eye
from heliograph.eye.live import follow_lines, health_line, read_last


def test_tail_no_follow_renders_events(isolated_home: Path) -> None:
    with eye.span("cdp.navigate"):
        pass
    with pytest.raises(RuntimeError), eye.span("uia.click"):
        raise RuntimeError("gone")
    buf = io.StringIO()
    eye.tail(follow=False, console=Console(file=buf, width=160))
    out = buf.getvalue()
    assert "cdp.navigate" in out and "RuntimeError: gone" in out and "errors 50%" in out


def test_read_last_and_follow(tmp_path: Path) -> None:
    path = tmp_path / "e.jsonl"
    path.write_text('{"name": "a"}\nnot json\n{"name": "b"}\n', encoding="utf-8")
    assert [e["name"] for e in read_last(path, 1)] == ["b"]
    gen = follow_lines(path, poll=0)
    assert next(gen) is None
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"name": "c"}\n')
    assert next(gen) == {"name": "c"}


def test_health_line_empty() -> None:
    assert "no spans" in health_line([]).plain
