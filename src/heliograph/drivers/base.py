"""The interface shared by Heliograph's Instagram drivers.

Two drivers implement :class:`InstagramDriver` (see ``docs/ARCHITECTURE.md``):

* ``uia`` — drives the installed Microsoft Store Instagram app through Windows UI Automation.
* ``cdp`` — drives a dedicated Edge/Chrome profile over the Chrome DevTools Protocol.

The protocol only covers what both can do; drivers add their own richer methods. Driver
methods should run inside :func:`heliograph.eye.span` and raise
:class:`heliograph.errors.HeliographError` subclasses (``DriverUnavailableError``,
``NotLoggedInError``, ``ElementNotFoundError``...) rather than raw library errors.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

__all__ = ["InstagramDriver"]


@runtime_checkable
class InstagramDriver(Protocol):
    """Asynchronous control surface over one Instagram client (app window or browser)."""

    name: str
    """Short identifier, e.g. ``"uia"`` or ``"cdp"``."""

    async def is_available(self) -> bool:
        """Return True if this driver can run here (platform, app/browser present).

        Must not raise and must not start anything.
        """
        ...

    async def ensure_ready(self) -> None:
        """Launch/attach to the client and wait until Instagram is loaded.

        Raises:
            DriverUnavailableError: the client cannot be started or attached.
            NotLoggedInError: Instagram is showing the login page.
        """
        ...

    async def current_url(self) -> str | None:
        """URL currently displayed, or None if the driver cannot tell."""
        ...

    async def navigate(self, url: str) -> None:
        """Open ``url`` (must be an ``https://www.instagram.com/...`` URL)."""
        ...

    async def screenshot(self, path: Path) -> Path:
        """Save a PNG screenshot of the client to ``path`` and return the path written."""
        ...

    async def snapshot(self) -> dict[str, Any]:
        """Return a JSON-serialisable summary of what is on screen.

        For ``uia`` an accessibility-tree summary, for ``cdp`` a DOM/ARIA summary. Should
        include at least ``{"driver": name, "url": ..., "title": ..., "nodes": [...]}``.
        """
        ...

    async def close(self) -> None:
        """Release resources (detach; never close the user's own Instagram app)."""
        ...
