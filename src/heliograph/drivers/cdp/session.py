"""``CdpDriver``: the browser (Chrome DevTools Protocol) implementation of InstagramDriver.

Attaches with Playwright's ``connect_over_cdp`` to the dedicated profile started by
:class:`~heliograph.drivers.cdp.launcher.BrowserLauncher`. Closing the driver only
disconnects; the browser window (and the user's session) stays open.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from heliograph.config import Settings, get_settings
from heliograph.drivers.cdp.capture import DEFAULT_PATTERNS, NetworkCapture
from heliograph.drivers.cdp.launcher import DEFAULT_ACCOUNT, INSTAGRAM_URL, BrowserLauncher
from heliograph.drivers.cdp.snapshot import SNAPSHOT_JS
from heliograph.drivers.cdp.webapi import WebApiClient
from heliograph.errors import (
    BrowserNotFoundError,
    DriverUnavailableError,
    HeliographError,
    NotLoggedInError,
)
from heliograph.eye import ActiveSpan, attach_artifact, span
from heliograph.instagram.endpoints import is_instagram_url as _strict_is_instagram_url

if TYPE_CHECKING:
    from playwright.async_api import Browser, BrowserContext, Page, Playwright

__all__ = ["CdpDriver", "is_instagram_url"]

LOGIN_URL = "https://www.instagram.com/accounts/login/"


def is_instagram_url(url: str) -> bool:
    """True for ``https://www.instagram.com/...`` (or the bare domain).

    Delegates to :func:`heliograph.instagram.endpoints.is_instagram_url`, which also refuses
    userinfo, odd ports and parser-differential characters (backslash, whitespace).
    """
    return _strict_is_instagram_url(url)


class CdpDriver:
    """Drive Instagram in a dedicated Chromium profile over CDP.

    Args:
        account: Profile/account key (see :class:`BrowserLauncher`).
        settings: Settings override.
        launcher: Launcher override (tests).
        launch: If False, only attach to an already-running browser.
    """

    name = "cdp"

    def __init__(
        self,
        account: str = DEFAULT_ACCOUNT,
        *,
        settings: Settings | None = None,
        launcher: BrowserLauncher | None = None,
        launch: bool = True,
    ) -> None:
        self.account = account
        self.settings = settings or get_settings()
        self.launcher = launcher or BrowserLauncher(account=account, settings=self.settings)
        self.launch = launch
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._page: Page | None = None
        self._api: WebApiClient | None = None

    # -- lifecycle ------------------------------------------------------------------
    async def is_available(self) -> bool:
        """True if a browser is running for this profile or one can be launched."""
        try:
            if await asyncio.to_thread(self.launcher.find_running):
                return True
            from heliograph.drivers.cdp.launcher import find_browser_executable

            await asyncio.to_thread(find_browser_executable, self.settings.browser_channel)
            return True
        except (BrowserNotFoundError, OSError):
            return False

    async def connect(self, *, create_page: bool = True) -> Page:
        """Attach to the browser (launching it if allowed) and return the Instagram page."""
        if self._page is not None and not self._page.is_closed():
            return self._page
        async with span("cdp.connect", account=self.account) as s:
            launcher = self.launcher
            finder = launcher.ensure_running if self.launch else launcher.find_running
            endpoint = await asyncio.to_thread(finder)
            if endpoint is None:
                raise DriverUnavailableError(
                    "No Heliograph browser is running for this profile",
                    hint="Run `heliograph login` to start it.")
            from playwright.async_api import async_playwright

            if self._pw is None:
                self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.connect_over_cdp(endpoint.http_url)
            self._page = self._find_page() or (await self._new_page() if create_page else None)
            if self._page is None:
                raise DriverUnavailableError("No Instagram window is open in the browser")
            s.set(port=endpoint.port, url=self._page.url)
            return self._page

    def _find_page(self) -> Page | None:
        assert self._browser is not None
        pages = [p for c in self._browser.contexts for p in c.pages if is_instagram_url(p.url)]
        return pages[0] if pages else None

    async def _new_page(self) -> Page:
        assert self._browser is not None
        ctx: BrowserContext = self._browser.contexts[0]
        page = await ctx.new_page()
        await page.goto(INSTAGRAM_URL, wait_until="domcontentloaded")
        return page

    async def ensure_ready(self) -> None:
        """Attach, and raise :class:`NotLoggedInError` if Instagram is not logged in."""
        await self.connect()
        if not await self.is_logged_in():
            raise NotLoggedInError("The Heliograph browser profile is not logged in to Instagram")

    @property
    def page(self) -> Page:
        """The attached Instagram page (call :meth:`connect` first)."""
        if self._page is None:
            raise DriverUnavailableError("CdpDriver is not connected; call connect() first")
        return self._page

    def api(self) -> WebApiClient:
        """A :class:`WebApiClient` bound to the Instagram page."""
        if self._api is None or self._api.page is not self.page:
            self._api = WebApiClient(self.page, account=self.account)
        return self._api

    async def close(self) -> None:
        """Disconnect from the browser without closing it."""
        with span("cdp.close"):
            if self._pw is not None:
                await self._pw.stop()  # drops the CDP connection; browser keeps running
            self._pw = self._browser = self._page = None
            self._api = None

    # -- InstagramDriver -----------------------------------------------------------
    async def current_url(self) -> str | None:
        """URL of the Instagram page, or None when not connected."""
        return self._page.url if self._page is not None else None

    async def navigate(self, url: str, *, wait_until: str = "domcontentloaded") -> None:
        """Open an ``https://www.instagram.com`` URL in the Instagram page."""
        if not is_instagram_url(url):
            raise ValueError(f"Refusing to navigate outside Instagram: {url!r}")
        async with self._action("cdp.navigate", url=url):
            await self.page.goto(url, wait_until=wait_until)  # type: ignore[arg-type]

    async def screenshot(self, path: Path) -> Path:
        """Save a PNG of the Instagram page's viewport."""
        async with self._action("cdp.screenshot", path=str(path)):
            path.parent.mkdir(parents=True, exist_ok=True)
            await self.page.screenshot(path=str(path))
            return path

    async def snapshot(self, *, max_text: int = 4000, max_nodes: int = 150) -> dict[str, Any]:
        """Compact DOM summary: url, title, visible text, links/buttons/inputs."""
        async with self._action("cdp.snapshot") as s:
            data: dict[str, Any] = await self.page.evaluate(SNAPSHOT_JS, [max_text, max_nodes])
            data["driver"] = self.name
            s.set(nodes=len(data.get("nodes", [])), text_len=len(data.get("text", "")))
            return data

    # -- session state -------------------------------------------------------------
    async def is_logged_in(self) -> bool:
        """True if the profile holds an Instagram ``sessionid`` cookie and isn't on login."""
        async with span("cdp.is_logged_in") as s:
            cookies = await self.page.context.cookies("https://www.instagram.com")
            has_session = any(c.get("name") == "sessionid" and c.get("value") for c in cookies)
            on_login = "/accounts/login" in self.page.url
            s.set(has_session=has_session, on_login=on_login)
            return has_session and not on_login

    async def viewer_id(self) -> str | None:
        """Numeric id of the logged-in user (``ds_user_id`` cookie), if present."""
        cookies = await self.page.context.cookies("https://www.instagram.com")
        return next((str(c["value"]) for c in cookies if c.get("name") == "ds_user_id"), None)

    async def login_interactive(self, *, timeout: float = 300.0, poll: float = 2.0) -> bool:
        """Show the login page and wait for the user to sign in themselves.

        Heliograph never types credentials. Returns True once logged in; False on timeout.
        """
        async with span("cdp.login_interactive", timeout=timeout) as s:
            await self.connect()
            if await self.is_logged_in():
                s.set(already=True)
                return True
            if "/accounts/login" not in self.page.url:
                await self.page.goto(LOGIN_URL, wait_until="domcontentloaded")
            await self.page.bring_to_front()
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                await asyncio.sleep(poll)
                if await self.is_logged_in():
                    s.set(logged_in=True)
                    return True
            s.set(logged_in=False)
            return False

    @asynccontextmanager
    async def capture(
        self, patterns: tuple[str, ...] = DEFAULT_PATTERNS
    ) -> AsyncIterator[NetworkCapture]:
        """Record Instagram JSON responses during the ``async with`` block."""
        async with NetworkCapture(self.page, patterns) as cap:
            yield cap

    # -- failure artifacts ---------------------------------------------------------
    @asynccontextmanager
    async def _action(self, name: str, **attrs: Any) -> AsyncIterator[ActiveSpan]:
        async with span(name, **attrs) as s:
            try:
                yield s
            except Exception as exc:
                if not isinstance(exc, ValueError | HeliographError):
                    await self._save_failure_artifacts(name)
                raise

    async def _save_failure_artifacts(self, name: str) -> None:
        if self._page is None or self._page.is_closed():
            return
        folder = self.settings.eye_path / "artifacts"
        stem = f"{time.strftime('%Y%m%d-%H%M%S')}-{name.replace('.', '_')}"
        try:
            self.settings.ensure_dir(folder)  # owner-only (failure artifacts are personal)
            shot = folder / f"{stem}.png"
            await self._page.screenshot(path=str(shot), timeout=5000)
            attach_artifact(shot, "screenshot")
            dom = await self._page.evaluate(SNAPSHOT_JS, [4000, 150])
            dom_path = folder / f"{stem}.dom.json"
            dom_path.write_text(json.dumps(dom, ensure_ascii=False, indent=1), encoding="utf-8")
            attach_artifact(dom_path, "dom")
        except Exception:  # artifacts are best effort
            return
