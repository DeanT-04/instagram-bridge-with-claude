"""Fixtures for extraction tests."""

from __future__ import annotations

import pytest

from heliograph.config import get_settings


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "slow: slow tests (e.g. loading a Whisper model)")


@pytest.fixture(autouse=True)
def _no_real_ocr(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dossier tests stay deterministic: OCR is off unless a test injects an engine."""
    monkeypatch.setenv("HELIOGRAPH_OCR_ENGINE", "off")
    get_settings.cache_clear()
