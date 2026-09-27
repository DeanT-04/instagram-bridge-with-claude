"""Repository wiring that is easy to break silently: .mcp.json and the setup scripts."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_mcp_json_runs_the_locked_environment() -> None:
    server = json.loads((ROOT / ".mcp.json").read_text("utf-8"))["mcpServers"]["heliograph"]
    assert server["command"] == "uv"
    assert server["args"] == ["run", "--locked", "--quiet", "heliograph", "mcp"]


def test_setup_scripts_pin_the_same_uv_installer_version() -> None:
    ps1 = (ROOT / "scripts" / "setup.ps1").read_text("utf-8")
    sh = (ROOT / "scripts" / "setup.sh").read_text("utf-8")
    ps_ver = re.search(r"\$UvVersion = '([\d.]+)'", ps1)
    sh_ver = re.search(r"^UV_VERSION=([\d.]+)$", sh, re.M)
    assert ps_ver and sh_ver and ps_ver.group(1) == sh_ver.group(1)
    assert "astral.sh/uv/install." not in ps1 + sh  # never the unpinned "latest" URL
    assert '[switch]$NoInput' in ps1 and "'--no-input'" in ps1
    assert "--no-input) NO_INPUT=1" in sh and '"$@" --no-input' in sh


def test_setup_sh_help_prints_exactly_the_header_comment() -> None:
    lines = (ROOT / "scripts" / "setup.sh").read_text("utf-8").splitlines()
    first, last = map(int, re.search(r"sed -n '(\d+),(\d+)p'", "\n".join(lines)).groups())  # type: ignore[union-attr]
    shown = lines[first - 1:last]
    assert all(line.startswith("#") for line in shown)
    assert not lines[last].startswith("#")  # nothing of the header is cut off
    assert any("--no-input" in line for line in shown)
