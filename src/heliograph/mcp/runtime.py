"""Lazily created drivers and services shared by every MCP tool call.

Nothing is launched when the server starts: the dedicated browser (CDP) is found or
started on the first Instagram tool call, and the Store-app driver (UIA) is attached on
the first ``app_*`` call. Factories are injectable so tests can run without a browser.

The browser's DevTools port lets any local process drive the logged-in session, so it
should not stay open longer than needed: a browser the server launched is closed on
shutdown (:meth:`Runtime.aclose`) and, optionally, after ``browser_idle_minutes`` without
tool calls (:meth:`Runtime.close_if_idle`); the next Instagram call relaunches it.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from heliograph.config import Settings, get_settings
from heliograph.errors import DriverUnavailableError
from heliograph.eye import span
from heliograph.mcp.common import tools_in_flight

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
        clock: Callable[[], float] = time.monotonic,
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
        self._clock = clock
        self._last_used = clock()
        self._idle_task: asyncio.Task[None] | None = None

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
        self.touch()
        driver = self.cdp_driver()
        await driver.connect()
        return driver

    async def service(self) -> InstagramService:
        """The logged-in :class:`InstagramService` (built on first use, rebuilt if the
        Instagram window was closed)."""
        async with self._lock:
            self.touch()
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

    # -- browser lifetime -------------------------------------------------------------
    def touch(self) -> None:
        """Record a use of the browser (resets the idle timer)."""
        self._last_used = self._clock()

    async def close_if_idle(self) -> bool:
        """Once no tool has run for ``browser_idle_minutes``, disconnect and close the
        browser if this server launched it. Returns True if the connection was dropped."""
        limit = self.settings.browser_idle_minutes * 60
        if limit <= 0 or self._cdp is None:
            return False
        async with self._lock:  # service() waits here, then relaunches transparently
            if tools_in_flight() > 0:  # a running call may be using the browser
                self.touch()
                return False
            idle = self._clock() - self._last_used
            if self._cdp is None or idle < limit:
                return False
            driver, self._cdp, self._service = self._cdp, None, None
            with span("mcp.browser_idle_close", idle_s=round(idle)) as s:
                try:
                    s.set(stopped=await driver.shutdown())
                except Exception as exc:  # best effort; the next call reconnects anyway
                    s.set(error_type=type(exc).__name__)
            return True

    def start_idle_watch(self) -> None:
        """Start the background idle check (the MCP server calls this on startup)."""
        if self._idle_task is None and self.settings.browser_idle_minutes > 0:
            self._idle_task = asyncio.get_running_loop().create_task(self._idle_loop())

    async def _idle_loop(self) -> None:
        while True:
            limit = self.settings.browser_idle_minutes * 60
            await asyncio.sleep(min(60.0, max(1.0, limit / 4)))
            with contextlib.suppress(Exception):
                await self.close_if_idle()

    async def aclose(self) -> None:
        """Detach from everything. The dedicated browser is closed only if this server
        launched it; a reused browser and the Store app are never closed."""
        if self._idle_task is not None:
            self._idle_task.cancel()
            with contextlib.suppress(BaseException):
                await self._idle_task
            self._idle_task = None
        if self._cdp is not None:
            with contextlib.suppress(Exception):  # best effort on shutdown
                await self._cdp.shutdown()
        if self._uia is not None:
            with contextlib.suppress(Exception):
                await self._uia.close()
        self._cdp = self._uia = None
        self._service = None

    def reset_service(self) -> None:
        """Forget the cached service (e.g. after the user logs in)."""
        self._service = None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        state: dict[str, Any] = {"cdp": self._cdp is not None, "uia": self._uia is not None}
        return f"Runtime(account={self.account!r}, {state})"
