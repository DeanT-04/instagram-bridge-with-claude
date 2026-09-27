"""Lazily created drivers and services shared by every MCP tool call.

Nothing is launched when the server starts: the dedicated browser (CDP) is found or
started on the first Instagram tool call, and the Store-app driver (UIA) is attached on
the first ``app_*`` call. Factories are injectable so tests can run without a browser.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from heliograph.config import Settings, get_settings
from heliograph.errors import DriverUnavailableError

if TYPE_CHECKING:
    from heliograph.drivers.cdp import CdpDriver
    from heliograph.drivers.uia import UiaDriver
    from heliograph.instagram.service import InstagramService

__all__ = ["Runtime"]

ServiceFactory = Callable[["Runtime"], Awaitable["InstagramService"]]
UiaFactory = Callable[[], Awaitable["UiaDriver"]]
CdpFactory = Callable[[str], "CdpDriver"]


def _default_cdp(account: str) -> CdpDriver:
    from heliograph.drivers.cdp import CdpDriver

    return CdpDriver(account)


async def _default_service(rt: Runtime) -> InstagramService:
    from heliograph.instagram.service import InstagramService

    return await InstagramService.from_driver(await rt.cdp())


async def _default_uia() -> UiaDriver:
    if sys.platform != "win32":
        raise DriverUnavailableError(
            "The live-app (UIA) tools only work on Windows",
            hint="Use the ig_* tools instead; they work on every OS through the browser.",
        )
    from heliograph.drivers.uia import UiaDriver

    driver = UiaDriver()
    await driver.ensure_ready()
    return driver


class Runtime:
    """Holds the process-wide CDP driver, Instagram service and UIA driver.

    Args:
        account: Browser-profile account key for the CDP driver.
        settings: Settings override.
        cdp_factory / service_factory / uia_factory: Injected for tests.
    """

    def __init__(
        self,
        account: str = "default",
        *,
        settings: Settings | None = None,
        cdp_factory: CdpFactory | None = None,
        service_factory: ServiceFactory | None = None,
        uia_factory: UiaFactory | None = None,
    ) -> None:
        self.account = account
        self._settings = settings
        self._cdp_factory = cdp_factory or _default_cdp
        self._service_factory = service_factory or _default_service
        self._uia_factory = uia_factory or _default_uia
        self._cdp: CdpDriver | None = None
        self._service: InstagramService | None = None
        self._uia: UiaDriver | None = None
        self._lock = asyncio.Lock()
        self._uia_lock = asyncio.Lock()

    @property
    def settings(self) -> Settings:
        """Settings in effect (resolved lazily so tests can change the environment)."""
        return self._settings or get_settings()

    async def cdp_connected(self) -> bool:
        """True if the CDP driver exists and is attached to an Instagram page."""
        if self._cdp is None:
            return False
        try:
            return await self._cdp.current_url() is not None
        except Exception:
            return False

    @property
    def uia_attached(self) -> bool:
        """True if the UIA driver has been attached."""
        return self._uia is not None

    def cdp_driver(self) -> CdpDriver:
        """The (possibly not yet connected) CDP driver singleton."""
        if self._cdp is None:
            self._cdp = self._cdp_factory(self.account)
        return self._cdp

    async def cdp(self) -> CdpDriver:
        """The CDP driver, connected (launching the dedicated browser if needed)."""
        driver = self.cdp_driver()
        await driver.connect()
        return driver

    async def service(self) -> InstagramService:
        """The logged-in :class:`InstagramService` (built on first use, rebuilt if the
        Instagram window was closed)."""
        async with self._lock:
            if self._service is not None and not self._page_closed():
                return self._service
            self._service = await self._service_factory(self)
            return self._service

    def _page_closed(self) -> bool:
        if self._cdp is None:
            return False
        try:
            return bool(self._cdp.page.is_closed())
        except Exception:
            return True

    async def uia(self) -> UiaDriver:
        """The Store-app driver, attached (launching the app if it is not open)."""
        async with self._uia_lock:
            if self._uia is None:
                self._uia = await self._uia_factory()
            return self._uia

    async def aclose(self) -> None:
        """Detach from everything (never closes the user's windows)."""
        for closer in (self._cdp, self._uia):
            if closer is not None:
                try:
                    await closer.close()
                except Exception:  # best effort on shutdown
                    pass
        self._cdp = self._uia = None
        self._service = None

    def reset_service(self) -> None:
        """Forget the cached service (e.g. after the user logs in)."""
        self._service = None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        state: dict[str, Any] = {"cdp": self._cdp is not None, "uia": self._uia is not None}
        return f"Runtime(account={self.account!r}, {state})"
