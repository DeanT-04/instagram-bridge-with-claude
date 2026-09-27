"""Write actions (like, save, follow, comment) of the UIA driver.

Every write requires ``confirm=True`` (else :class:`UnsafeActionError`), checks that the
target ref really is the expected control (by name) in the current snapshot, and reserves a
slot of the cross-process :class:`~heliograph.writelimit.SharedWriteLimiter` (the same
budget as CDP writes; :class:`RateLimitedError` when too soon). The generic ``click``
refuses write controls, and :func:`unsafe_key_reason` guards keystrokes (Enter in a
comment/message box posts it) so neither can bypass confirmation.
"""

from __future__ import annotations

import asyncio
import re
import unicodedata
from typing import Any

from heliograph.drivers.uia import actions
from heliograph.drivers.uia.core import UiaCore
from heliograph.drivers.uia.tree import Node, Snapshot
from heliograph.errors import ElementNotFoundError, RateLimitedError, UnsafeActionError
from heliograph.writelimit import SharedWriteLimiter

__all__ = [
    "WRITE_CONTROL_NAMES",
    "WriteActions",
    "is_compose_box",
    "is_submit_keys",
    "is_write_control",
    "normalize_control_name",
    "unsafe_key_reason",
]

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


_WRITE_NAMES_FOLDED = frozenset(n.casefold() for n in WRITE_CONTROL_NAMES)
# Leading verbs that mark a control as account-changing even with a suffix
# ("Unfollow alice", "Delete comment", "Report post", "Block user"...).
_WRITE_VERBS = frozenset({
    "like", "unlike", "follow", "unfollow", "save", "unsave", "remove", "repost", "post",
    "send", "share", "delete", "block", "unblock", "report", "restrict", "unrestrict",
    "unsend", "mute", "unmute", "requested", "following", "log", "logout",
})


def normalize_control_name(name: str) -> str:
    """Canonical form for write-control matching: NFKC, no format/zero-width characters,
    collapsed whitespace, case-folded (so "LIKE", "Like​" and "Follow back" match)."""
    text = unicodedata.normalize("NFKC", name or "")
    text = "".join(ch for ch in text if unicodedata.category(ch) not in ("Cf", "Cc"))
    return " ".join(text.split()).casefold()


def _is_write_name(name: str) -> bool:
    norm = normalize_control_name(name)
    if not norm:
        return False
    first = norm.split(" ", 1)[0]
    return norm in _WRITE_NAMES_FOLDED or first in _WRITE_VERBS


def is_write_control(node: Node, snap: Snapshot | None = None) -> bool:
    """True if pressing ``node`` could change the account (like, follow, post...).

    Any role counts (a click on the ``image "Like"`` icon inside a button lands on the
    button), names are compared after :func:`normalize_control_name`, and with ``snap``
    the node's ancestors are checked too (clicking inside a write button presses it).
    """
    if _is_write_name(node.name):
        return True
    if snap is None:
        return False
    seen: set[str] = set()
    parent = node.parent
    while parent is not None and parent not in seen:
        seen.add(parent)
        anc = snap.get(parent)
        if anc is None:
            break
        if _is_write_name(anc.name):
            return True
        parent = anc.parent
    return False


# Enter/Return (any modifiers: {Ctrl}{Enter}...) or a raw newline submits a focused box;
# Space also activates a focused button.
_SUBMIT_KEYS = re.compile(r"\{\s*(enter|return)\b[^}]*\}|[\r\n]", re.IGNORECASE)
_ACTIVATE_KEYS = re.compile(r"\{\s*space\b[^}]*\}| ", re.IGNORECASE)
# Tab (or a raw tab character) moves focus, e.g. from a message box to its Send button, so
# "hi<Tab><Space>" would press a control the focus check never saw.
_FOCUS_MOVE_KEYS = re.compile(r"\{\s*tab\b[^}]*\}|\t", re.IGNORECASE)
_COMPOSE_WORDS = re.compile(r"comment|message|reply|caption|chat|write|note|send", re.I)
_SAFE_FIELDS = re.compile(r"search", re.IGNORECASE)


def is_submit_keys(keys: str) -> bool:
    """True if ``keys`` (uiautomation SendKeys syntax, or typed text) contains Enter."""
    return bool(_SUBMIT_KEYS.search(keys or ""))


def is_compose_box(role: str, name: str) -> bool:
    """True for a comment/message/caption field (or any text field that is not a search
    box): pressing Enter there posts/sends."""
    if _SAFE_FIELDS.search(name or ""):
        return False
    return role == "edit" or bool(_COMPOSE_WORDS.search(name or "") and role != "hyperlink")


def unsafe_key_reason(
    keys: str, focused: tuple[str, str] | None, *, write_control: bool | None = None
) -> str | None:
    """Why sending ``keys`` to the ``(role, name)`` element with focus could change the
    account without confirmation, or None if it cannot. ``write_control`` overrides the
    name-based write-control test (e.g. from :func:`is_write_control` with ancestors)."""
    submit = is_submit_keys(keys)
    activate = submit or bool(_ACTIVATE_KEYS.search(keys or ""))
    if not activate:
        return None
    if _FOCUS_MOVE_KEYS.search(keys or ""):
        return "Tab moves focus to another control (e.g. Send) that Space/Enter would press"
    if focused is None:
        return "the focused element is unknown" if submit else None
    role, name = focused
    if submit and is_compose_box(role, name):
        return f"Enter in {role} {name!r} can post a comment or send a message"
    if write_control if write_control is not None else _is_write_name(name):
        return f"the focused control {name!r} changes the account"
    return None


class WriteActions(UiaCore):
    """Confirm-gated write actions on top of :class:`UiaCore`."""

    write_limiter: SharedWriteLimiter = SharedWriteLimiter(name="uia.write")
    """Shared with CDP writes (``~/.heliograph/ratelimit.json``); tests may replace it."""

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
        """Refuse without ``confirm``; fail fast if the shared write budget is exhausted."""
        if not confirm:
            raise UnsafeActionError(f"{op} changes your Instagram account; pass confirm=True")
        wait = self.write_limiter.remaining()
        if wait > 0:
            raise RateLimitedError(f"{op}: writes are rate limited", retry_after=wait)

    def _reserve_write(self, op: str) -> None:
        """(just before acting) Take the shared write slot, atomically across processes."""
        self.write_limiter.try_acquire(op)

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
            self._reserve_write(op)
            method = await self._worker.run(actions.click, self._handle(ref))
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
            self._reserve_write("comment")
            await self._worker.run(actions.press_keys, "{Enter}")
            s.set(box=boxes[0].name)
            return {"ref": ref, "changed": True}
