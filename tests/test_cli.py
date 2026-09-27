from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

import heliograph.cli as cli
from heliograph import __version__, eye
from heliograph.detect.models import (
    BrowserInfo,
    EnvironmentReport,
    InstagramAppInfo,
    PackageInfo,
    ToolInfo,
)

runner = CliRunner()


def _report(browsers: bool) -> EnvironmentReport:
    return EnvironmentReport(
        os="windows", os_version="Windows-11", python_version="3.12.4",
        instagram_app=InstagramAppInfo(supported=True, installed=False, reason="not installed"),
        browsers=[BrowserInfo(channel="msedge", path="C:/edge.exe", source="registry")]
        if browsers else [],
        ffmpeg=ToolInfo(name="ffmpeg", found=True, version="9.0"),
        ffprobe=ToolInfo(name="ffprobe", found=False, reason="not on PATH"),
        playwright=PackageInfo(name="playwright", importable=True, version="1.0"),
        uiautomation=PackageInfo(name="uiautomation", importable=True),
        browser_profile_dir="C:/p", browser_profile_initialized=False,
    )


def test_version() -> None:
    result = runner.invoke(cli.app, ["version"])
    assert result.exit_code == 0 and __version__ in result.output


@pytest.mark.parametrize(("browsers", "code"), [(True, 0), (False, 1)])
def test_doctor_exit_code(monkeypatch: pytest.MonkeyPatch, browsers: bool, code: int) -> None:
    monkeypatch.setattr(cli, "detect_environment", lambda: _report(browsers))
    result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == code
    assert "heliograph setup" in result.output and "heliograph login" in result.output


def test_doctor_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "detect_environment", lambda: _report(True))
    result = runner.invoke(cli.app, ["doctor", "--json"])
    assert json.loads(result.output)["python_version"] == "3.12.4"


def test_eye_report_and_tail() -> None:
    with pytest.raises(ValueError), eye.span("x.fail"):
        raise ValueError("bad")
    result = runner.invoke(cli.app, ["eye", "report"])
    assert result.exit_code == 0 and "x.fail" in result.output
    result = runner.invoke(cli.app, ["eye", "report", "--json"])
    assert json.loads(result.output)["errors"] == 1
    result = runner.invoke(cli.app, ["eye", "--no-follow"])
    assert result.exit_code == 0 and "x.fail" in result.output
