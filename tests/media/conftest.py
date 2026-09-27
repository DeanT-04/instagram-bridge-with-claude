"""Fixtures for media pipeline tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.media.videogen import make_video


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "slow: slow tests (e.g. loading a Whisper model)")


@pytest.fixture(scope="session")
def video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A 4 s two-scene video with a speech (or sine) audio track."""
    return make_video(tmp_path_factory.mktemp("vid") / "syn.mp4")


@pytest.fixture(scope="session")
def silent_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The same video without any audio stream."""
    return make_video(tmp_path_factory.mktemp("vid") / "silent.mp4", audio=False)
