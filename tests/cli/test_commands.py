"""CLI commands: --help everywhere, setup with a mocked environment, login/extract fakes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from rich.console import Console
from typer.testing import CliRunner

import heliograph.cli as cli
from heliograph.commands import setup as setup_mod
from heliograph.commands.extract import run_extract
from heliograph.commands.login import run_login
from heliograph.config import get_settings
from heliograph.instagram.models import Media
from tests.mcp_server.conftest import REEL
from tests.mcp_server.test_status import report

runner = CliRunner()


@pytest.mark.parametrize("cmd", [[], ["setup"], ["doctor"], ["login"], ["mcp"], ["extract"],
                                 ["eye"], ["eye", "report"], ["version"]])
def test_help(cmd: list[str]) -> None:
    result = runner.invoke(cli.app, [*cmd, "--help"])
    assert result.exit_code == 0, result.output
    assert "Usage" in result.output


@pytest.fixture
def mocked_env(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    opened: list[str] = []
    monkeypatch.setattr(setup_mod, "detect_environment", lambda: report())
    monkeypatch.setattr(setup_mod, "install_instagram_app",
                        lambda: opened.append("store") or "Opened the Store page")
    return opened


def test_setup_declines_store(mocked_env: list[str]) -> None:
    result = runner.invoke(cli.app, ["setup"], input="n\n")
    assert result.exit_code == 0, result.output
    assert "winget install Gyan.FFmpeg" in result.output
    assert "heliograph login" in result.output and "claude" in result.output
    assert "no browser download needed" in result.output
    assert mocked_env == []


def test_setup_opens_store_when_confirmed(mocked_env: list[str]) -> None:
    result = runner.invoke(cli.app, ["setup"], input="y\n")
    assert result.exit_code == 0 and mocked_env == ["store"]
    assert "Opened the Store page" in result.output


def test_setup_no_input_never_prompts(mocked_env: list[str]) -> None:
    result = runner.invoke(cli.app, ["setup", "--no-input"])
    assert result.exit_code == 0 and mocked_env == []


def test_setup_with_whisper(mocked_env: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    loaded: list[str] = []
    monkeypatch.setattr(setup_mod, "_preload_whisper",
                        lambda console, model: loaded.append(model))
    result = runner.invoke(cli.app, ["setup", "--no-input", "--with-whisper"])
    assert result.exit_code == 0 and loaded == [get_settings().whisper_model]


class FakeDriver:
    def __init__(self, logged_in_after: bool) -> None:
        self.logged_in_after = logged_in_after
        self.closed = False
        self.login_called = False

    async def connect(self) -> None:
        pass

    async def is_logged_in(self) -> bool:
        return False

    async def login_interactive(self, *, timeout: float) -> bool:
        self.login_called = True
        return self.logged_in_after

    async def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize("ok", [True, False])
def test_login(ok: bool) -> None:
    console = Console(record=True, width=120)
    driver = FakeDriver(ok)
    assert run_login(console, driver=driver, timeout=1) is ok
    assert driver.login_called and driver.closed
    text = console.export_text()
    assert "password" in text and ("Logged in" in text if ok else "Timed out" in text)


class FakeService:
    def __init__(self) -> None:
        self.media_obj = Media.from_api(REEL)

    async def media(self, ref: str) -> Media:
        return self.media_obj

    async def iter_collection(self, collection: str, limit: int | None = None) -> Any:
        for _ in range(2):
            yield self.media_obj


def test_extract_single_and_collection(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[str] = []

    async def fake_build(media: Media, root: Path, **kw: Any) -> Any:
        built.append(media.code or "")
        folder = root / "o" / (media.code or "x")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "dossier.md").write_text("x", encoding="utf-8")

        class D:
            markdown_path = folder / "dossier.md"
        return D()

    monkeypatch.setattr("heliograph.extract.dossier.abuild_dossier", fake_build)
    console = Console(record=True, width=200)
    assert run_extract(console, ref=REEL["code"], collection=None, service=FakeService()) == 0
    assert run_extract(console, ref=None, collection="Trading strats", limit=2,
                       service=FakeService()) == 0
    assert built == [REEL["code"]] * 3
    assert "dossier.md" in console.export_text()
    assert run_extract(console, ref=None, collection=None) == 2
