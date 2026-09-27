"""Runtime configuration for Heliograph.

Settings are read from environment variables prefixed ``HELIOGRAPH_`` and from a ``.env``
file in the current directory. All paths default to locations under ``~/.heliograph`` and
are never relative to the working directory. Directories are created lazily via
:meth:`Settings.ensure_dir` so that importing the config has no side effects.
"""

from __future__ import annotations

import os
import stat
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["BrowserChannel", "Settings", "ensure_private_dir", "get_settings"]

BrowserChannel = Literal["auto", "msedge", "chrome", "chromium"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def ensure_private_dir(path: Path) -> Path:
    """Create ``path`` (and parents) if missing, restricting it to the current user.

    On POSIX the directory mode is set to ``0o700``. On Windows the user profile is already
    private by default, so only creation is performed. Returns ``path``.
    """
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        try:
            path.chmod(stat.S_IRWXU)
        except OSError:  # pragma: no cover - e.g. not the owner
            pass
    return path


class Settings(BaseSettings):
    """Heliograph settings (env prefix ``HELIOGRAPH_``).

    Path fields left unset are derived from :attr:`home`.
    """

    model_config = SettingsConfigDict(
        env_prefix="HELIOGRAPH_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    home: Path = Field(default_factory=lambda: Path.home() / ".heliograph")
    browser_profile_dir: Path | None = None
    dossier_dir: Path | None = None
    eye_dir: Path | None = None

    cdp_port: int = Field(default=0, ge=0, le=65535, description="0 = random free port")
    browser_channel: BrowserChannel = "auto"
    whisper_model: str = "small"

    read_min_interval: float = Field(default=1.5, ge=0, description="Seconds between reads")
    read_jitter: float = Field(default=1.0, ge=0, description="Random extra seconds on reads")
    write_min_interval: float = Field(default=20.0, ge=0, description="Seconds between writes")
    write_jitter: float = Field(default=10.0, ge=0, description="Random extra seconds on writes")

    eye_max_bytes: int = Field(default=10 * 1024 * 1024, gt=0, description="JSONL rotation size")
    eye_backups: int = Field(default=5, ge=0)

    log_level: LogLevel = "INFO"

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_level(cls, v: object) -> object:
        return v.upper() if isinstance(v, str) else v

    @field_validator("home", "browser_profile_dir", "dossier_dir", "eye_dir", mode="after")
    @classmethod
    def _expand(cls, v: Path | None) -> Path | None:
        return v.expanduser().resolve() if v is not None else None

    @model_validator(mode="after")
    def _derive_paths(self) -> Settings:
        if self.browser_profile_dir is None:
            self.browser_profile_dir = self.home / "browser-profile"
        if self.dossier_dir is None:
            self.dossier_dir = self.home / "dossiers"
        if self.eye_dir is None:
            self.eye_dir = self.home / "eye"
        return self

    # Typed accessors (the validator guarantees these are set).
    @property
    def profile_path(self) -> Path:
        """Resolved browser profile directory for the CDP driver."""
        assert self.browser_profile_dir is not None
        return self.browser_profile_dir

    @property
    def dossier_path(self) -> Path:
        """Resolved directory where extraction dossiers are written."""
        assert self.dossier_dir is not None
        return self.dossier_dir

    @property
    def eye_path(self) -> Path:
        """Resolved directory for eye logs, the SQLite index and artifacts."""
        assert self.eye_dir is not None
        return self.eye_dir

    @property
    def state_file(self) -> Path:
        """Path of ``state.json`` (e.g. the recorded DevTools port)."""
        return self.home / "state.json"

    def ensure_dir(self, path: Path) -> Path:
        """Create ``path`` with restrictive permissions and return it."""
        return ensure_private_dir(path)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide cached :class:`Settings`.

    Call ``get_settings.cache_clear()`` after changing the environment (tests do this).
    """
    return Settings()
