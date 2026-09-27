"""Exception hierarchy for Heliograph.

Every error raised deliberately by Heliograph derives from :class:`HeliographError`, so
callers (the CLI, the MCP server) can catch one type and render a helpful message.
Each error carries an optional ``hint`` telling the user how to fix the problem.
"""

from __future__ import annotations

__all__ = [
    "AppNotInstalledError",
    "BrowserNotFoundError",
    "DownloadBlockedError",
    "DriverUnavailableError",
    "ElementNotFoundError",
    "HeliographError",
    "NotLoggedInError",
    "RateLimitedError",
    "UnsafeActionError",
]


class HeliographError(Exception):
    """Base class for all Heliograph errors.

    Args:
        message: Human-readable description of what went wrong.
        hint: Optional actionable suggestion (e.g. "run `heliograph login`").
    """

    default_hint: str | None = None

    def __init__(self, message: str = "", *, hint: str | None = None) -> None:
        super().__init__(message or self.__class__.__doc__ or self.__class__.__name__)
        self.message = message
        self.hint = hint if hint is not None else self.default_hint

    def __str__(self) -> str:
        base = super().__str__()
        return f"{base} (hint: {self.hint})" if self.hint else base


class NotLoggedInError(HeliographError):
    """The Instagram session is not logged in."""

    default_hint = "Run `heliograph login` and sign in to Instagram in the window that opens."


class AppNotInstalledError(HeliographError):
    """The Microsoft Store Instagram app is not installed."""

    default_hint = "Run `heliograph setup` to open the Store page, or use the browser (cdp) driver."


class BrowserNotFoundError(HeliographError):
    """No supported Chromium browser (Edge/Chrome) was found."""

    default_hint = "Install Microsoft Edge or Google Chrome, or set HELIOGRAPH_BROWSER_CHANNEL."


class DriverUnavailableError(HeliographError):
    """The requested driver cannot run in this environment."""


class RateLimitedError(HeliographError):
    """An operation was refused by Heliograph's rate limiter or by Instagram.

    Args:
        retry_after: Seconds to wait before retrying, if known.
    """

    def __init__(
        self, message: str = "", *, retry_after: float | None = None, hint: str | None = None
    ) -> None:
        super().__init__(message, hint=hint)
        self.retry_after = retry_after


class UnsafeActionError(HeliographError):
    """A write action was attempted without explicit confirmation or violates a safety rule."""

    default_hint = "Write actions require confirm=True."


class DownloadBlockedError(HeliographError):
    """A download URL was rejected (non-HTTPS or host not in the Instagram CDN allow-list)."""


class ElementNotFoundError(HeliographError):
    """A UI element or DOM node could not be located."""
