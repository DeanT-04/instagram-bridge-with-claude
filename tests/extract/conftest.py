"""Fixtures for extraction tests."""

from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "slow: slow tests (e.g. loading a Whisper model)")
