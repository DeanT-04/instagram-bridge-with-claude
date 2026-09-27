from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from heliograph.config import Settings, ensure_private_dir, get_settings


def test_defaults_derive_from_home(isolated_home: Path) -> None:
    s = get_settings()
    assert s.home == isolated_home.resolve()
    assert s.profile_path == s.home / "browser-profile"
    assert s.dossier_path == s.home / "dossiers"
    assert s.eye_path == s.home / "eye"
    assert s.state_file == s.home / "state.json"
    assert s.cdp_port == 0 and s.browser_channel == "auto" and s.whisper_model == "small"


def test_dossier_dir_never_relative_to_cwd(tmp_path: Path) -> None:
    s = Settings()
    assert s.dossier_path.is_absolute()
    assert s.dossier_path != (tmp_path / "dossiers").resolve()


def test_env_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HELIOGRAPH_CDP_PORT", "9333")
    monkeypatch.setenv("HELIOGRAPH_BROWSER_CHANNEL", "chrome")
    monkeypatch.setenv("HELIOGRAPH_DOSSIER_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("HELIOGRAPH_LOG_LEVEL", "debug")
    monkeypatch.setenv("HELIOGRAPH_WRITE_MIN_INTERVAL", "30")
    s = Settings()
    assert s.cdp_port == 9333 and s.browser_channel == "chrome"
    assert s.dossier_path == (tmp_path / "d").resolve()
    assert s.log_level == "DEBUG" and s.write_min_interval == 30


def test_dotenv_file_is_read(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("HELIOGRAPH_WHISPER_MODEL=tiny\n", encoding="utf-8")
    assert Settings().whisper_model == "tiny"


@pytest.mark.parametrize(
    ("var", "value"),
    [("HELIOGRAPH_BROWSER_CHANNEL", "firefox"), ("HELIOGRAPH_CDP_PORT", "70000"),
     ("HELIOGRAPH_READ_JITTER", "-1")],
)
def test_invalid_values_rejected(monkeypatch: pytest.MonkeyPatch, var: str, value: str) -> None:
    monkeypatch.setenv(var, value)
    with pytest.raises(ValidationError):
        Settings()


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()


def test_no_dirs_created_on_load(isolated_home: Path) -> None:
    s = Settings()
    assert not s.profile_path.exists() and not s.dossier_path.exists()


def test_ensure_private_dir(tmp_path: Path) -> None:
    p = ensure_private_dir(tmp_path / "a" / "b")
    assert p.is_dir()
