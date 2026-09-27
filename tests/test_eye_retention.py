"""Eye failure-artifact retention (pruned at start-up) and owner-only artifacts folder."""

from __future__ import annotations

import os
import stat
import time
from pathlib import Path

import pytest

from heliograph import eye
from heliograph.config import get_settings
from heliograph.eye.core import Eye, prune_artifacts


def _aged(path: Path, days: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")
    when = time.time() - days * 86400
    os.utime(path, (when, when))
    return path


def test_prune_artifacts(tmp_path: Path) -> None:
    folder = tmp_path / "artifacts"
    old = _aged(folder / "uia-click-1.png", 10)
    old_nested = _aged(folder / "screenshots" / "app-1.png", 8)
    fresh = _aged(folder / "uia-click-2.txt", 1)
    assert prune_artifacts(folder, 7) == 2
    assert not old.exists() and not old_nested.exists() and fresh.exists()
    assert not (folder / "screenshots").exists()  # emptied subfolder removed
    assert prune_artifacts(folder, 0) == 0 and fresh.exists()  # 0 = keep everything
    assert prune_artifacts(tmp_path / "missing", 7) == 0


def test_eye_prunes_at_startup_with_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HELIOGRAPH_ARTIFACT_RETENTION_DAYS", "3")
    get_settings.cache_clear()
    eye.reset()
    art = get_settings().eye_path / "artifacts"
    old, fresh = _aged(art / "a.png", 4), _aged(art / "b.png", 2)
    recorder = eye.get_eye()
    assert recorder.pruned_artifacts == 1
    assert not old.exists() and fresh.exists()


def test_default_retention_is_seven_days() -> None:
    assert get_settings().artifact_retention_days == 7


def test_artifacts_dir_is_created_owner_only(tmp_path: Path) -> None:
    recorder = Eye(tmp_path / "eye", artifact_retention_days=7)
    assert recorder.artifacts_dir.is_dir()
    if os.name == "posix":
        assert stat.S_IMODE(recorder.artifacts_dir.stat().st_mode) == 0o700
    recorder.close()
