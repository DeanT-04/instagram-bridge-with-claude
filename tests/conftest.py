"""Shared fixtures: isolate settings and the eye into a temporary home per test."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from heliograph import eye
from heliograph.config import get_settings
from heliograph.detect.instagram_app import clear_package_cache

Recorded = Callable[..., list[dict[str, Any]]]


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point HELIOGRAPH_HOME at a temp dir and route the eye there."""
    home = tmp_path / "home"
    monkeypatch.setenv("HELIOGRAPH_HOME", str(home))
    for var in ("HELIOGRAPH_EYE_DIR", "HELIOGRAPH_DOSSIER_DIR", "HELIOGRAPH_BROWSER_PROFILE_DIR",
                "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)  # so a developer's .env is not picked up
    get_settings.cache_clear()
    clear_package_cache()
    eye.configure(home / "eye")
    yield home
    clear_package_cache()
    eye.reset()
    get_settings.cache_clear()


def _gha_escape(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """On GitHub Actions, surface each failure as a workflow annotation.

    Annotations are visible via the public checks API (job logs need a signed-in
    viewer), which makes cross-platform CI failures diagnosable from anywhere.
    """
    if not os.environ.get("GITHUB_ACTIONS") or not report.failed:
        return
    path, line, _ = report.location
    detail = str(report.longrepr)[-3000:]
    title = _gha_escape(report.nodeid).replace(":", "%3A").replace(",", "%2C")
    print(f"::error file={path},line={(line or 0) + 1},title={title}::{_gha_escape(detail)}",
          file=sys.__stdout__, flush=True)


def pytest_terminal_summary(terminalreporter: Any) -> None:
    """On GitHub Actions, list every failing test id in one annotation (errors cap at 10)."""
    failed = terminalreporter.stats.get("failed", []) + terminalreporter.stats.get("error", [])
    if os.environ.get("GITHUB_ACTIONS") and failed:
        ids = "\n".join(getattr(r, "nodeid", "?") for r in failed)
        print(f"::warning title={len(failed)} failing tests::{_gha_escape(ids)}",
              file=sys.__stdout__, flush=True)


@pytest.fixture
def recorded() -> Recorded:
    """Return a function listing events in the eye index (oldest first)."""

    def _recorded(**filters: Any) -> list[dict[str, Any]]:
        return list(reversed(eye.get_eye().query(**filters)))

    return _recorded
