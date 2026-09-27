"""Shared fixtures: isolate settings and the eye into a temporary home per test."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from heliograph import eye
from heliograph.config import get_settings

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
    eye.configure(home / "eye")
    yield home
    eye.reset()
    get_settings.cache_clear()


@pytest.fixture
def recorded() -> Recorded:
    """Return a function listing events in the eye index (oldest first)."""

    def _recorded(**filters: Any) -> list[dict[str, Any]]:
        return list(reversed(eye.get_eye().query(**filters)))

    return _recorded
