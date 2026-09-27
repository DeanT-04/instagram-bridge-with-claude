"""``UiaDriver``: the async InstagramDriver over the Microsoft Store Instagram app window.

Layers: :class:`~.core.UiaCore` (attach/launch, snapshot, URL navigation, screenshots)
-> :class:`~.writes.WriteActions` (confirm-gated like/save/follow/comment)
-> :class:`UiaDriver` (read-only interaction: click, type, keys, scroll, section nav,
readers). See ``docs/ARCHITECTURE.md`` and the module docstrings for live-verified details.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Literal

from heliograph.drivers.uia import actions, nav, read
from heliograph.drivers.uia.core import _norm, _same_place
from heliograph.drivers.uia.tree import Node
from heliograph.drivers.uia.writes import WriteActions, is_write_control
from heliograph.errors import ElementNotFoundError, UnsafeActionError

__all__ = ["UiaDriver"]


class UiaDriver(WriteActions):
    """Drive the Microsoft Store Instagram app through Windows UI Automation."""

    async def click(
        self,
        ref: str | None = None,
        *,
        name: str | None = None,
        role: str | None = None,
        exact: bool = True,
        prefer: actions.ClickMethod = "invoke",
    ) -> dict[str, Any]:
        """Click by ``ref`` from the last snapshot, or by ``name``/``role`` (fresh snapshot).

        Read-only use only: write controls (Like/Follow/Save/...) go through the
        ``confirm=True`` methods of :class:`~.writes.WriteActions`.
        """
        async with self._span("click", ref=ref, target_name=name, role=role) as s:
            if ref is None:
                await self._worker.run(self._snapshot_sync)
            node = self._resolve(ref, name, role, exact)
            if is_write_control(node):
                raise UnsafeActionError(
                    f"{node.name!r} [{node.ref}] is a write control; use the dedicated "
                    "method (like/save/follow/comment) with confirm=True"
                )
            if prefer == "mouse":
                await self.require_foreground()
            try:
                method = await self._worker.run(
                    actions.click, self._handle(node.ref), prefer=prefer
                )
            except Exception:
                if ref is not None:
                    raise
                # element went stale mid-transition: re-resolve by name once
                await asyncio.sleep(0.5)
                await self._worker.run(self._snapshot_sync)
                node = self._resolve(None, name, role, exact)
                method = await self._worker.run(
                    actions.click, self._handle(node.ref), prefer=prefer
                )
            s.set(target=node.name, clicked_ref=node.ref, method=method)
            return {"ref": node.ref, "name": node.name, "role": node.role, "method": method}

    async def type_text(
        self,
        text: str,
        ref: str | None = None,
        *,
        name: str | None = None,
        role: str | None = "edit",
        clear: bool = False,
        submit: bool = False,
    ) -> None:
        """Type into a field (``ref``/``name``) or the focused element; Enter if ``submit``."""
        async with self._span("type_text", chars=len(text), ref=ref, target_name=name):
            await self.require_foreground()
            if ref is None and name is None:
                await self._worker.run(actions.type_text, text, None, clear=clear)
            for attempt in range(2 if ref is None and name is not None else 1):
                if ref is not None or name is not None:
                    if ref is None:
                        await self._worker.run(self._snapshot_sync)
                    handle = self._handle(self._resolve(ref, name, role).ref)
                    try:
                        await self._worker.run(actions.type_text, text, handle, clear=clear)
                        break
                    except Exception:
                        if attempt or ref is not None:
                            raise
                        await asyncio.sleep(0.5)  # stale element mid-transition: retry
            if submit:
                await self._worker.run(actions.press_keys, "{Enter}")

    async def press(self, keys: str) -> None:
        """Send keys to the app (uiautomation syntax: ``{Esc}``, ``{Ctrl}a``, ``{PageDown}``)."""
        async with self._span("press", keys=keys):
            await self.require_foreground()
            await self._worker.run(actions.press_keys, keys)

    def _scroll_sync(self, direction: Literal["up", "down"], pages: int) -> str | None:
        """(worker) Scroll the document, else the Reels next/previous buttons, else the
        largest scrollable container."""
        doc = self._document()
        if actions.scroll_element(doc, direction, pages):
            return "document"
        snap = self._snapshot_sync()
        label = "Navigate to next reel" if direction == "down" else "Navigate to previous reel"
        reel_buttons = snap.find(label, "button")
        if reel_buttons:  # the Reels viewer: one reel per "page"
            for _ in range(pages):
                actions.click(snap.handle(reel_buttons[0].ref))
                time.sleep(0.6)
            return "reel_button"
        view = snap.viewport or (0, 0, 1, 1)
        min_area = 0.2 * (view[2] - view[0]) * (view[3] - view[1])

        def area(n: Node) -> int:
            return (n.rect[2] - n.rect[0]) * (n.rect[3] - n.rect[1])

        boxes = [
            n
            for n in snap.nodes
            if n.role in ("group", "main", "list", "region", "dialog") and area(n) >= min_area
        ]
        for node in sorted(boxes, key=area, reverse=True):
            if actions.scroll_element(snap.handle(node.ref), direction, pages):
                return f"container:{node.ref}"
        return None

    async def scroll(self, direction: Literal["up", "down"] = "down", pages: int = 1) -> str:
        """Scroll ``pages`` screens up/down; returns the mechanism used.

        ``"document"`` (feed, profile), ``"reel_button"`` (Reels viewer: one reel per page,
        via its "Navigate to next/previous reel" buttons), ``"container:eN"`` (an inner
        scrollable) or ``"keys"`` (PageDown/PageUp after bringing the window forward).
        """
        if direction not in ("up", "down"):
            raise ValueError("direction must be 'up' or 'down'")
        async with self._span("scroll", direction=direction, pages=pages) as s:
            method = await self._worker.run(self._scroll_sync, direction, pages)
            if method is None:
                await self.require_foreground()
                doc = await self._worker.run(self._document)
                await self._worker.run(actions.scroll_keys, doc, direction, pages)
                method = "keys"
            s.set(method=method)
            await self.wait_settled(timeout=3.0)
            return method

    async def navigate_section(self, section: str, *, timeout: float = 15.0) -> dict[str, Any]:
        """Open a left-nav section: home, search, explore, reels, messages, notifications,
        create (only opens the dialog), profile, saved."""
        async with self._span("navigate_section", section=section) as s:
            snap = await self._worker.run(self._snapshot_sync)
            target = nav.resolve_section(snap, section)
            closers = snap.find("Close", "button", visible=True)
            if target.node is None and closers and section != "saved":
                # an overlay panel (notifications...) hides the nav rail: close it first
                await self._worker.run(actions.click, snap.handle(closers[0].ref))
                snap = await self.wait_settled(timeout=3.0) or snap
                target = nav.resolve_section(snap, section)
            if (
                section == "saved"
                and target.url
                and not any(_norm(n.value) == _norm(target.url) for n in snap.nodes)
            ):  # reach the profile first so its "Saved" tab link allows in-place navigation
                await self.navigate_section("profile", timeout=timeout)
            node = target.node
            if node is not None:
                dest = node.value if node.value and not node.value.endswith("#") else None
                for attempt in range(3):  # a click during a page transition is sometimes lost
                    await self._worker.run(actions.click, snap.handle(node.ref))
                    if dest is None:
                        break
                    got = await self._wait_url(lambda u: _same_place(u, dest), timeout / 3)
                    if _same_place(got, dest):
                        break
                    s.set(retries=attempt + 1)
                    snap = await self.wait_settled(timeout=2.0) or snap
                    node = nav.resolve_section(snap, section).node or node
                await self.wait_settled()
                url = await self._worker.run(self._url_sync)
                reached = dest is None or _same_place(url, dest)
                s.set(method="nav_link", url=url, reached=reached)
                if not reached:
                    raise ElementNotFoundError(f"clicked {section!r} but the page did not change")
                return {"section": section, "method": "nav_link", "url": url}
            if target.url is None:
                raise ElementNotFoundError(f"cannot find the {section!r} section in this view")
            result = await self.navigate_url(target.url, timeout=timeout)
            s.set(method=result["method"], url=result["url"])
            return {"section": section, **result}

    async def visible_posts(self, *, visible_only: bool = True) -> list[dict[str, Any]]:
        """Posts/reels on screen (fresh snapshot); see :func:`read.visible_posts`."""
        snap = await self.take_snapshot()
        async with self._span("visible_posts") as s:
            posts = read.visible_posts(snap, visible_only=visible_only)
            s.set(count=len(posts))
            return posts

    async def unread_badges(self) -> dict[str, int]:
        """Unread message/notification counts from the nav."""
        return read.unread_badges(await self.take_snapshot())

    async def current_section(self) -> str:
        """Where the app is (home, reels, profile, saved, search...)."""
        snap = await self.take_snapshot()
        return read.current_section(snap, nav.own_username(snap))
