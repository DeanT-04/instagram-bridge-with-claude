"""Write actions (like, save, follow, comment) of the UIA driver.

Every write requires ``confirm=True`` (else :class:`UnsafeActionError`), checks that the
target ref really is the expected control (by name) in the current snapshot, and is
throttled by ``settings.write_min_interval`` (:class:`RateLimitedError`). The generic
``click`` refuses write controls so they cannot be pressed by accident.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, ClassVar

from heliograph.config import get_settings
from heliograph.drivers.uia import actions
from heliograph.drivers.uia.core import UiaCore
from heliograph.drivers.uia.tree import Node
from heliograph.errors import ElementNotFoundError, RateLimitedError, UnsafeActionError

__all__ = ["WRITE_CONTROL_NAMES", "WriteActions", "is_write_control"]

WRITE_CONTROL_NAMES = frozenset(
    {
        "Like",
        "Unlike",
        "Follow",
        "Following",
        "Follow Back",
        "Unfollow",
        "Save",
        "Remove",
        "Repost",
        "Post",
        "Send",
        "Share",
        "Delete",
        "Block",
        "Report",
        "Restrict",
        "Remove follower",
        "Unsend",
        "Add to collection",
    }
)


def is_write_control(node: Node) -> bool:
    """True if pressing ``node`` could change the account (like, follow, post...)."""
    return node.role in ("button", "hyperlink", "menuitem") and node.name in WRITE_CONTROL_NAMES


class WriteActions(UiaCore):
    """Confirm-gated write actions on top of :class:`UiaCore`."""

    _last_write: ClassVar[float] = 0.0

    def _resolve(
        self, ref: str | None, name: str | None, role: str | None, exact: bool = True
    ) -> Node:
        snap = self._snap
        if snap is None:
            raise ElementNotFoundError("no snapshot yet", hint="Call take_snapshot() first.")
        if ref is not None:
            node = snap.get(ref)
            if node is None:
                raise ElementNotFoundError(f"unknown ref {ref!r}", hint="Take a new snapshot.")
            return node
        matches = snap.find(name, role, exact=exact)
        visible = [n for n in matches if n.visible]
        if not matches:
            raise ElementNotFoundError(f"no element with name={name!r} role={role!r}")
        return (visible or matches)[0]

    def _handle(self, ref: str) -> Any:
        """Live element for ``ref`` in the last snapshot."""
        if self._snap is None:
            raise ElementNotFoundError("no snapshot yet", hint="Call take_snapshot() first.")
        return self._snap.handle(ref)

    def _check_write(self, op: str, confirm: bool) -> None:
        if not confirm:
            raise UnsafeActionError(f"{op} changes your Instagram account; pass confirm=True")
        interval = get_settings().write_min_interval
        wait = WriteActions._last_write + interval - time.monotonic()
        if WriteActions._last_write and wait > 0:
            raise RateLimitedError(
                f"{op}: writes are limited to one per {interval:.0f}s", retry_after=wait
            )

    async def _press_write(
        self, op: str, ref: str, wanted: set[str], done: set[str], confirm: bool
    ) -> dict[str, Any]:
        self._check_write(op, confirm)
        async with self._span(op, ref=ref) as s:
            node = self._resolve(ref, None, None)
            if node.name in done:
                s.set(changed=False)
                return {"ref": ref, "changed": False, "state": node.name}
            if node.name not in wanted:
                raise ElementNotFoundError(
                    f"{ref} is {node.role} {node.name!r}, expected one of {sorted(wanted)}",
                    hint="Take a new snapshot; refs change when the page changes.",
                )
            method = await self._worker.run(actions.click, self._handle(ref))
            WriteActions._last_write = time.monotonic()
            s.set(changed=True, method=method)
            return {"ref": ref, "changed": True, "method": method}

    async def like(self, ref: str, *, confirm: bool = False) -> dict[str, Any]:
        """Like the post whose Like button is ``ref`` (from ``visible_posts()[i]["refs"]``)."""
        return await self._press_write("like", ref, {"Like"}, {"Unlike"}, confirm)

    async def unlike(self, ref: str, *, confirm: bool = False) -> dict[str, Any]:
        """Remove a like (``ref`` is the button now named "Unlike")."""
        return await self._press_write("unlike", ref, {"Unlike"}, {"Like"}, confirm)

    async def save(self, ref: str, *, confirm: bool = False) -> dict[str, Any]:
        """Save (bookmark) the post whose Save button is ``ref``."""
        return await self._press_write("save", ref, {"Save"}, {"Remove"}, confirm)

    async def unsave(self, ref: str, *, confirm: bool = False) -> dict[str, Any]:
        """Un-save (``ref`` is the button now named "Remove")."""
        return await self._press_write("unsave", ref, {"Remove"}, {"Save"}, confirm)

    async def follow(self, ref: str, *, confirm: bool = False) -> dict[str, Any]:
        """Follow the account whose Follow button is ``ref``."""
        return await self._press_write(
            "follow", ref, {"Follow", "Follow Back"}, {"Following"}, confirm
        )

    async def comment(self, ref: str, text: str, *, confirm: bool = False) -> dict[str, Any]:
        """Post ``text`` as a comment; ``ref`` is the post's Comment button."""
        self._check_write("comment", confirm)
        if not text.strip():
            raise ValueError("comment text is empty")
        async with self._span("comment", ref=ref, chars=len(text)) as s:
            node = self._resolve(ref, None, None)
            if not node.name.startswith("Comment"):
                raise ElementNotFoundError(f"{ref} is {node.name!r}, not a Comment button")
            await self.require_foreground()
            await self._worker.run(actions.click, self._handle(ref))
            await asyncio.sleep(1.0)
            snap = await self._worker.run(self._snapshot_sync)
            boxes = [n for n in snap.nodes if n.role == "edit" and "comment" in n.name.lower()]
            if not boxes:
                raise ElementNotFoundError("comment box did not appear")
            await self._worker.run(actions.type_text, text, snap.handle(boxes[0].ref))
            await self._worker.run(actions.press_keys, "{Enter}")
            WriteActions._last_write = time.monotonic()
            s.set(box=boxes[0].name)
            return {"ref": ref, "changed": True}
