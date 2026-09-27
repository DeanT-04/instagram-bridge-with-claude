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
from heliograph.drivers.uia.writes import WriteActions, is_write_control, unsafe_key_reason
from heliograph.errors import ElementNotFoundError, UnsafeActionError

__all__ = ["UiaDriver"]


class UiaDriver(WriteActions):
    """Drive the Microsoft Store Instagram app through Windows UI Automation."""

    def _refuse_write_control(self, node: Node) -> None:
        if is_write_control(node, self._snap):
            raise UnsafeActionError(
                f"{node.name!r} [{node.ref}] is (or is inside) a write control; use the "
                "dedicated method (like/save/follow/comment) with confirm=True"
            )

    async def click(
        self,
        ref: str | None = None,
        *,
        name: str | None = None,
        role: str | None = None,
        exact: bool = True,
        prefer: actions.ClickMethod = "invoke",
        allow_large: bool = False,
    ) -> dict[str, Any]:
        """Click by ``ref`` from the last snapshot, or by ``name``/``role`` (fresh snapshot).

        Read-only use only: write controls (Like/Follow/Save/...) go through the
        ``confirm=True`` methods of :class:`~.writes.WriteActions`. The mouse fallback
        refuses container-sized elements unless ``allow_large`` (see :mod:`.actions`).
        """
        async with self._span("click", ref=ref, target_name=name, role=role) as s:
            if ref is None:
                await self._worker.run(self._snapshot_sync)
            node = self._resolve(ref, name, role, exact)
            self._refuse_write_control(node)
            if prefer == "mouse":
                await self.require_foreground()
            try:
                method = await self._worker.run(
                    actions.click, self._handle(node.ref), prefer=prefer, allow_large=allow_large
                )
            except UnsafeActionError:
                raise
            except Exception:
                if ref is not None:
                    raise
                # element went stale mid-transition: re-resolve by name once
                await asyncio.sleep(0.5)
                await self._worker.run(self._snapshot_sync)
                node = self._resolve(None, name, role, exact)
                self._refuse_write_control(node)
                method = await self._worker.run(
                    actions.click, self._handle(node.ref), prefer=prefer, allow_large=allow_large
                )
            s.set(target=node.name, clicked_ref=node.ref, method=method)
            return {"ref": node.ref, "name": node.name, "role": node.role, "method": method}

    async def _guard_keys(self, keys: str, confirm: bool, target: Node | None = None) -> bool:
        """Refuse keystrokes that could post/send/press a write control without ``confirm``.

        ``target`` is the element the keys go to, else the element with keyboard focus
        (the window must already be in the foreground). Returns True when the keys are a
        confirmed write, so the caller takes a shared write-rate-limit slot.
        """
        if target is not None:
            focused: tuple[str, str] | None = (target.role, target.name)
            write_control = is_write_control(target, self._snap)
        else:
            focused = await self._worker.run(actions.focused_element)
            write_control = None
        reason = unsafe_key_reason(keys, focused, write_control=write_control)
        if reason is None:
            return False
        if not confirm:
            raise UnsafeActionError(
                f"refusing to send {keys!r}: {reason}",
                hint="Ask the user for explicit confirmation, then pass confirm=True.",
            )
        return True

    async def type_text(
        self,
        text: str,
        ref: str | None = None,
        *,
        name: str | None = None,
        role: str | None = "edit",
        clear: bool = False,
        submit: bool = False,
        confirm: bool = False,
    ) -> None:
        """Type into a field (``ref``/``name``) or the focused element; Enter if ``submit``.

        ``submit`` (or a newline in ``text``) into a comment/message box, or typing into a
        write control, raises :class:`UnsafeActionError` unless ``confirm=True`` (which
        then also takes a shared write-rate-limit slot).
        """
        async with self._span("type_text", chars=len(text), ref=ref, target_name=name):
            keys = text + ("{Enter}" if submit else "")
            target = None
            if ref is not None or name is not None:
                if ref is None:
                    await self._worker.run(self._snapshot_sync)
                target = self._resolve(ref, name, role)
                is_write = await self._guard_keys(keys, confirm, target)
                await self.require_foreground()
            else:
                await self.require_foreground()
                is_write = await self._guard_keys(keys, confirm)
            if is_write:
                self._reserve_write("type_text")
            if target is None:
                await self._worker.run(actions.type_text, text, None, clear=clear)
            else:
                try:
                    await self._worker.run(
                        actions.type_text, text, self._handle(target.ref), clear=clear
                    )
                except Exception:
                    if ref is not None:
                        raise
                    await asyncio.sleep(0.5)  # stale element mid-transition: retry by name
                    await self._worker.run(self._snapshot_sync)
                    retry = self._resolve(None, name, role)
                    if (retry.role, retry.name) != (target.role, target.name):
                        raise
                    await self._worker.run(
                        actions.type_text, text, self._handle(retry.ref), clear=clear
                    )
            if submit:
                await self._worker.run(actions.press_keys, "{Enter}")

    async def press(self, keys: str, *, confirm: bool = False) -> None:
        """Send keys to the app (uiautomation syntax: ``{Esc}``, ``{Ctrl}a``, ``{PageDown}``).

        Enter (any modifiers) while a comment/message box has focus, or Enter/Space on a
        focused write control, raises :class:`UnsafeActionError` unless ``confirm=True``.
        """
        async with self._span("press", keys=keys):
            await self.require_foreground()
            if await self._guard_keys(keys, confirm):
                self._reserve_write("press")
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

    async def visible_posts(
        self, *, visible_only: bool = True, wait: float = 8.0
    ) -> list[dict[str, Any]]:
        """Posts/reels/grid tiles on screen (fresh snapshot); see :func:`read.visible_posts`.

        The web view renders lazily: right after launch or navigation the document can be
        nearly empty or show ``Loading...`` placeholders. While that is the case (or the
        section normally shows posts but none are found yet) snapshots are retried for up
        to ``wait`` seconds instead of reporting zero posts.
        """
        deadline = time.monotonic() + max(0.0, wait)
        attempts = 0
        while True:
            snap = await self.take_snapshot()
            attempts += 1
            posts = read.visible_posts(snap, visible_only=visible_only)
            if posts or time.monotonic() >= deadline:
                break
            section = read.current_section(snap, nav.own_username(snap))
            if not (read.is_loading(snap) or section in read.POST_SECTIONS):
                break
            await asyncio.sleep(0.5)
        async with self._span("visible_posts") as s:
            s.set(count=len(posts), attempts=attempts, nodes=len(snap))
            return posts

    async def unread_badges(self) -> dict[str, int]:
        """Unread message/notification counts from the nav."""
        return read.unread_badges(await self.take_snapshot())

    async def current_section(self) -> str:
        """Where the app is (home, reels, profile, saved, search...)."""
        snap = await self.take_snapshot()
        return read.current_section(snap, nav.own_username(snap))
