"""Pydantic models describing the detected environment."""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = [
    "BrowserInfo",
    "EnvironmentReport",
    "InstagramAppInfo",
    "PackageInfo",
    "ToolInfo",
]

INSTAGRAM_PACKAGE = "Facebook.InstagramBeta"
INSTAGRAM_AUMID = "Facebook.InstagramBeta_8xx8rvfyw5nnt!App"
INSTAGRAM_STORE_PRODUCT_ID = "9NBLGGH5L9XT"
NOT_INSTALLED = "not installed"  # InstagramAppInfo.reason when Get-AppxPackage found nothing


class InstagramAppInfo(BaseModel):
    """The Microsoft Store Instagram app (an Edge PWA) — Windows only."""

    supported: bool = Field(description="False on non-Windows platforms")
    installed: bool = False
    name: str | None = None
    version: str | None = None
    package_family_name: str | None = None
    aumid: str | None = None
    install_location: str | None = None
    window_open: bool | None = Field(default=None, description="None if it could not be checked")
    window_title: str | None = None
    reason: str | None = Field(default=None, description="Why a check failed or was skipped")


class BrowserInfo(BaseModel):
    """A Chromium-family browser usable by the CDP driver."""

    channel: str = Field(description="msedge | chrome | chromium")
    path: str
    source: str = Field(description="Where it was found: registry, path, common-location")


class ToolInfo(BaseModel):
    """An external executable such as ffmpeg."""

    name: str
    found: bool
    path: str | None = None
    version: str | None = None
    reason: str | None = None


class PackageInfo(BaseModel):
    """An importable Python package."""

    name: str
    importable: bool
    version: str | None = None
    reason: str | None = None


class EnvironmentReport(BaseModel):
    """Everything ``heliograph doctor`` knows about this machine."""

    os: str = Field(description="windows | macos | linux | other")
    os_version: str
    python_version: str
    instagram_app: InstagramAppInfo
    browsers: list[BrowserInfo] = Field(default_factory=list)
    ffmpeg: ToolInfo
    ffprobe: ToolInfo
    playwright: PackageInfo
    uiautomation: PackageInfo
    browser_profile_dir: str
    browser_profile_initialized: bool = Field(
        description="True if the dedicated CDP browser profile has been created (login ran)"
    )
    logged_in: bool | None = Field(
        default=None,
        description="Last observed login state of the dedicated profile (None = never checked)",
    )

    @property
    def needs_login(self) -> bool:
        """True when ``heliograph login`` still has to be run (or run again)."""
        return not self.browser_profile_initialized or self.logged_in is False

    @property
    def preferred_browser(self) -> BrowserInfo | None:
        """First detected browser (Edge preferred, then Chrome, then Chromium)."""
        return self.browsers[0] if self.browsers else None

    @property
    def critical_missing(self) -> list[str]:
        """Names of missing requirements without which Heliograph cannot run at all."""
        missing: list[str] = []
        if not self.browsers:
            missing.append("browser")
        if not self.playwright.importable:
            missing.append("playwright")
        return missing

    @property
    def ok(self) -> bool:
        """True when nothing critical is missing."""
        return not self.critical_missing
