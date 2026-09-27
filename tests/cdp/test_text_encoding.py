"""Emoji survive the page -> Python path (no U+FFFD introduced by Heliograph)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from heliograph.drivers.cdp.snapshot import SNAPSHOT_JS
from heliograph.drivers.cdp.webapi import _decode


def test_api_json_surrogate_escapes_decode_to_emoji() -> None:
    """Instagram escapes non-BMP characters as surrogate pairs; json.loads joins them."""
    body = '{"collection_name": "get your s**t together \\ud83d\\ude24", "status": "ok"}'
    data = _decode(200, body, {"url": ""}, "/api/v1/x/")
    assert data["collection_name"] == "get your s**t together \U0001f624"
    assert "�" not in data["collection_name"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_snapshot_truncation_never_splits_a_surrogate_pair(tmp_path: Path) -> None:
    start, end = SNAPSHOT_JS.index("const cut"), SNAPSHOT_JS.index("const clean")
    script = tmp_path / "cut.js"
    script.write_text(
        SNAPSHOT_JS[start:end]
        + "const s = 'ab' + String.fromCodePoint(0x1F624);\n"
        + "process.stdout.write(JSON.stringify([cut(s, 2), cut(s, 3), cut(s, 4)]));\n",
        "utf-8",
    )
    out = subprocess.run(
        [shutil.which("node") or "node", str(script)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    assert json.loads(out.stdout) == ["ab", "ab", "ab\U0001f624"]
