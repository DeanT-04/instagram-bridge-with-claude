"""``UiaCore``: plumbing + InstagramDriver protocol methods of the UIA driver.

Every public method runs in an eye span (``uia.<op>``); on error the span's hook saves a
window screenshot plus the last snapshot outline and attaches them as artifacts. All UIA
work is funnelled through one COM-initialised worker thread (:class:`UiaWorker`).

Navigation strategy (live-verified, Edge 154 / Store app 42.0.23.0):

1. In place: if the current page has a hyperlink to the target URL, invoke it (SPA nav).
2. Otherwise open a new app window at the URL
   (``msedge --app-id=... --app-launch-url-for-shortcuts-menu-item=URL``), copy the old
   window's placement onto it and close the old window. PWA windows have no omnibox and
   Ctrl+L does nothing, so this is the only way to reach an arbitrary URL.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import Any

from heliograph import eye
from heliograph.config import get_settings
from heliograph.drivers.uia import actions, app, source
from heliograph.drivers.uia.runtime import UiaWorker
from heliograph.drivers.uia.screenshot import capture_window
from heliograph.drivers.uia.tree import Snapshot
from heliograph.errors import (
    DriverUnavailableError,
    NotLoggedInError,
    UnsafeActionError,
)
from heliograph.instagram.endpoints import is_instagram_url

__all__ = ["UiaCore"]

_IG_PREFIX = "https://www.instagram.com/"


def _norm(url: str | None) -> str:
    return (url or "").split("#")[0].split("?")[0].rstrip("/")


def _same_place(url: str | None, target: str) -> bool:
    """True if ``url`` is ``target`` or below it (``/reels/`` redirects to ``/reels/<id>/``)."""
    got, want = _norm(url), _norm(target)
    if got == want:
        return True
    return bool(want) and want != _norm(_IG_PREFIX) and got.startswith(want + "/")


class UiaCore:
    """Window attachment, snapshots, URL navigation and screenshots (see UiaDriver)."""

    name = "uia"

    def __init__(self, *, launch: bool = True, ready_timeout: float = 30.0) -> None:
        self.launch_if_needed = launch
        self.ready_timeout = ready_timeout
        self._worker = UiaWorker()
        self._hwnd: int | None = None
        self._doc: Any = None
        self._snap: Snapshot | None = None

    # ------------------------------------------------------------------ plumbing
    def _span(self, op: str, **attrs: Any) -> eye.span:
        return eye.span(f"uia.{op}", on_error=self._on_error, **attrs)

    def _on_error(self, active: Any, exc: BaseException) -> None:
        if self._hwnd is None:
            return
        settings = get_settings()
        folder = settings.ensure_dir(settings.eye_path / "artifacts")  # owner-only
        stem = f"uia-{active.name.split('.')[-1]}-{time.strftime('%Y%m%d-%H%M%S')}"
        try:
            path, _ = capture_window(self._hwnd, folder / f"{stem}.png")
            eye.attach_artifact(path, "screenshot")
        except Exception as shot_exc:
            active.attrs["failure_screenshot_error"] = repr(shot_exc)
        if self._snap is not None:
            text = folder / f"{stem}.txt"
            text.write_text(self._snap.render_text(visible_only=False, max_nodes=None), "utf-8")
            eye.attach_artifact(text, "uia_snapshot")

    def _require_hwnd(self) -> int:
        if self._hwnd is None:
            raise DriverUnavailableError("UiaDriver not ready; call ensure_ready() first")
        return self._hwnd

    def _document(self) -> Any:
        """(worker thread) The web document control, re-found if the old one went stale."""
        hwnd = self._require_hwnd()
        if self._doc is not None:
            try:
                if self._doc.Exists(0, 0):
                    return self._doc
            except Exception:
                pass
        self._doc = source.find_document(hwnd)
        return self._doc

    def _url_sync(self) -> str | None:
        url = source.document_url(self._document())
        if not url:  # the document element is replaced on full loads: find it again
            self._doc = None
            url = source.document_url(self._document())
        return url

    def _snapshot_sync(self) -> Snapshot:
        snap = source.read_snapshot(self._document())
        self._snap = snap
        return snap

    async def _wait_url(self, predicate: Any, timeout: float = 10.0) -> str | None:
        deadline = time.monotonic() + timeout
        url = None
        while time.monotonic() < deadline:
            url = await self._worker.run(self._url_sync)
            if predicate(url):
                return url
            await asyncio.sleep(0.25)
        return url

    async def wait_settled(self, timeout: float = 6.0, interval: float = 0.3) -> Snapshot | None:
        """Wait until two consecutive snapshots agree on URL and node count (page loaded).

        Returns the last snapshot (None if none could be taken before ``timeout``).
        """
        deadline = time.monotonic() + timeout
        last: Snapshot | None = None
        while time.monotonic() < deadline:
            snap = await self._worker.run(self._snapshot_sync)
            if (
                last is not None
                and len(snap) > 15
                and snap.url == last.url
                and len(snap) == len(last)
            ):
                return snap
            last = snap
            await asyncio.sleep(interval)
        return last

    # ------------------------------------------------------------------ protocol
    async def is_available(self) -> bool:
        """True on Windows with uiautomation importable and the Store app installed."""
        if sys.platform != "win32":
            return False
        try:
            return await self._worker.run(app.is_installed)
        except Exception:
            return False

    async def ensure_ready(self) -> None:
        """Attach to (or launch) the Store app window and wait for Instagram to load."""
        async with self._span("ensure_ready") as s:
            windows = await self._worker.run(app.find_app_windows)
            if not windows:
                if not self.launch_if_needed:
                    raise DriverUnavailableError("Instagram app is not running")
                await self._worker.run(app.launch)
                win = await self._worker.run(app.wait_for_new_window, set(), self.ready_timeout)
                s.set(launched=True)
            else:
                win = windows[0]
            if self._hwnd != win.hwnd:
                self._hwnd, self._doc = win.hwnd, None
            if win.minimized:
                await self._worker.run(app.set_show_state, win.hwnd, "restore")
            url = await self._wait_url(
                lambda u: bool(u and u.startswith(_IG_PREFIX)), self.ready_timeout
            )
            s.set(hwnd=win.hwnd, url=url)
            if not url or not url.startswith(_IG_PREFIX):
                raise DriverUnavailableError(f"Instagram did not load in the app (url={url!r})")
            if "/accounts/login" in url:
                raise NotLoggedInError(
                    "the Instagram app shows the login page",
                    hint="Log in inside the Instagram app window.",
                )
            # the URL is known long before the page renders (a freshly launched window's
            # document has ~5 nodes): wait for real content so readers don't see nothing
            snap = await self.wait_settled(timeout=min(self.ready_timeout, 15.0))
            s.set(nodes=len(snap) if snap is not None else 0)

    async def current_url(self) -> str | None:
        """URL of the page (from the web document's ValuePattern)."""
        async with self._span("current_url") as s:
            url = await self._worker.run(self._url_sync)
            s.set(url=url)
            return url

    async def navigate(self, url: str) -> None:
        """Open an ``https://www.instagram.com/...`` URL (see module docstring)."""
        await self.navigate_url(url)

    async def navigate_url(self, url: str, *, timeout: float = 20.0) -> dict[str, Any]:
        """Like :meth:`navigate` but returns ``{url, method}`` (method: noop|link|new_window)."""
        if not url.startswith(_IG_PREFIX) or not is_instagram_url(url):
            raise UnsafeActionError(f"refusing to navigate outside instagram.com: {url!r}")
        async with self._span("navigate", url=url) as s:
            snap = await self._worker.run(self._snapshot_sync)
            if _norm(snap.url) == _norm(url):
                s.set(method="noop")
                return {"url": snap.url, "method": "noop"}
            links = [
                n for n in snap.nodes if n.role == "hyperlink" and _norm(n.value) == _norm(url)
            ]
            if links:
                await self._worker.run(actions.click, snap.handle(links[0].ref))
                got = await self._wait_url(lambda u: _norm(u) == _norm(url), timeout / 2)
                if _norm(got) == _norm(url):
                    await self.wait_settled()
                    s.set(method="link")
                    return {"url": got, "method": "link"}
            got = await self._open_in_new_window(url, timeout)
            s.set(method="new_window", final_url=got)
            return {"url": got, "method": "new_window"}

    async def _open_in_new_window(self, url: str, timeout: float) -> str | None:
        old = self._require_hwnd()
        known = {w.hwnd for w in await self._worker.run(app.find_app_windows)}
        await self._worker.run(app.launch, url)
        new = await self._worker.run(app.wait_for_new_window, known, timeout)
        await self._worker.run(app.copy_placement, old, new.hwnd)
        self._hwnd, self._doc, self._snap = new.hwnd, None, None
        got = await self._wait_url(lambda u: _norm(u) == _norm(url), timeout)
        await self._worker.run(app.close_window, old)
        await self.wait_settled()
        return got

    async def screenshot(self, path: Path) -> Path:
        """PNG of the app window only (works while occluded); attached to the span."""
        async with self._span("screenshot", path=str(path)) as s:
            hwnd = self._require_hwnd()
            windows = {w.hwnd: w for w in await self._worker.run(app.find_app_windows)}
            if hwnd in windows and windows[hwnd].minimized:
                await self._worker.run(app.set_show_state, hwnd, "restore")
                await asyncio.sleep(0.5)
            out, method = await asyncio.to_thread(capture_window, hwnd, Path(path))
            s.set(method=method)
            eye.attach_artifact(out, "screenshot")
            return out

    async def snapshot(
        self,
        *,
        interactive_only: bool = False,
        visible_only: bool = False,
        max_nodes: int | None = None,
    ) -> dict[str, Any]:
        """JSON-able accessibility snapshot ``{driver, url, title, nodes: [...]}``."""
        snap = await self.take_snapshot()
        return snap.to_dict(
            interactive_only=interactive_only, visible_only=visible_only, max_nodes=max_nodes
        )

    async def close(self) -> None:
        """Detach (stop the worker thread). Never closes the user's app window."""
        self._worker.shutdown()
        self._doc = self._snap = None

    # ------------------------------------------------------------------ extras
    @property
    def last_snapshot(self) -> Snapshot | None:
        """The most recent snapshot: the one the ``eN`` refs of click/like/... refer to."""
        return self._snap

    async def take_snapshot(self) -> Snapshot:
        """Fresh :class:`Snapshot`; its refs are valid until the next snapshot."""
        async with self._span("snapshot") as s:
            snap = await self._worker.run(self._snapshot_sync)
            s.set(nodes=len(snap), raw=snap.raw_count, took_ms=round(snap.took_ms, 1))
            return snap

    async def render(self, **kwargs: Any) -> str:
        """Fresh snapshot rendered as an LLM outline (see :meth:`Snapshot.render_text`)."""
        return (await self.take_snapshot()).render_text(**kwargs)

    async def activate(self, *, maximize: bool = False) -> bool:
        """Bring the app window to the foreground (optionally maximised)."""
        async with self._span("activate", maximize=maximize) as s:
            hwnd = self._require_hwnd()
            if maximize:
                await self._worker.run(app.set_show_state, hwnd, "maximize")
            ok = await self._worker.run(app.activate, hwnd)
            s.set(foreground=ok)
            return ok

    async def require_foreground(self) -> None:
        """Activate the window and make sure it really is in front before keystrokes or
        mouse input, so input can never land in another application."""
        ok = await self.activate()
        if not ok:
            await asyncio.sleep(0.3)
            ok = await self.activate()
        if not ok:
            raise DriverUnavailableError(
                "could not bring the Instagram window to the foreground; refusing to send input",
                hint="Click the Instagram window once, then retry.",
            )

    async def close_app(self) -> None:
        """Close the app window itself (explicit request only)."""
        async with self._span("close_app"):
            await self._worker.run(app.close_window, self._require_hwnd())
            self._hwnd = self._doc = self._snap = None
