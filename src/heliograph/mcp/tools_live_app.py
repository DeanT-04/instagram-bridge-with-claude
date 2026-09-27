"""Live-app tools: drive the user's installed Microsoft Store Instagram app (Windows, UIA)."""

from __future__ import annotations

import re
import time
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP, Image

from heliograph.errors import ElementNotFoundError, UnsafeActionError
from heliograph.mcp.common import READ_ONLY, WRITE, tool
from heliograph.mcp.runtime import Runtime

__all__ = ["WRITE_VERBS", "register"]

WRITE_VERBS = re.compile(
    r"\b(like|unlike|follow|unfollow|save|unsave|remove|comment|send|post|delete|share|"
    r"repost|block|report|restrict|unsend|reply|submit)\b",
    re.IGNORECASE,
)
# Store-app control name -> confirm-gated driver method.
_WRITE_METHODS = {"Like": "like", "Unlike": "unlike", "Save": "save", "Remove": "unsave",
                  "Follow": "follow", "Follow Back": "follow"}
Section = Literal["home", "search", "explore", "reels", "messages", "notifications",
                  "profile", "saved"]


def _dry_run(what: str, why: str) -> dict[str, Any]:
    return {"performed": False, "dry_run": True, "would": what, "reason": why,
            "next_step": "Describe this to the user and ask for explicit confirmation in chat; "
                         "only if they say yes, call again with confirm=true."}


def _last_snapshot(driver: Any) -> Any:
    # UiaDriver keeps the snapshot its refs belong to; prefer a public accessor if present.
    return getattr(driver, "last_snapshot", None) or getattr(driver, "_snap", None)


def register(server: FastMCP, rt: Runtime) -> None:
    """Register the live-app tools."""

    @tool(server, annotations=READ_ONLY)
    async def app_open(maximize: bool = False) -> dict[str, Any]:
        """Attach to the user's Instagram app window (Microsoft Store app, Windows only),
        launching it if it is not open, and bring it to the front. Returns the current
        URL and section. Uses the user's real, already-logged-in app session."""
        driver = await rt.uia()
        await driver.ensure_ready()
        await driver.activate(maximize=maximize)
        return {"url": await driver.current_url(), "section": await driver.current_section()}

    @tool(server, annotations=READ_ONLY)
    async def app_snapshot(interactive_only: bool = False, visible_only: bool = True,
                           max_nodes: int = 300) -> str:
        """Text outline of what is on screen in the Instagram app (accessibility tree),
        one element per line like `- button "Like" [e12]`. The [eN] refs are what
        app_click / app_type take; they are only valid until the next snapshot or page
        change, so snapshot again after navigating or scrolling."""
        driver = await rt.uia()
        snap = await driver.take_snapshot()
        return str(snap.render_text(interactive_only=interactive_only,
                                    visible_only=visible_only, max_nodes=max_nodes))

    @tool(server, annotations=READ_ONLY)
    async def app_screenshot() -> list[Any]:
        """Screenshot of the Instagram app window (works even if it is behind other
        windows) returned as an image you can see. Also saved under the eye artifacts."""
        driver = await rt.uia()
        folder = rt.settings.ensure_dir(rt.settings.eye_path / "artifacts" / "screenshots")
        path = await driver.screenshot(folder / f"app-{time.strftime('%Y%m%d-%H%M%S')}.png")
        return [f"Screenshot saved to {path}", Image(path=path)]

    @tool(server, annotations=READ_ONLY)
    async def app_navigate(
        section: Section | None = None, url: str | None = None
    ) -> dict[str, Any]:
        """Open a section of the Instagram app (home, search, explore, reels, messages,
        notifications, profile, saved) or an https://www.instagram.com/... URL (a post,
        reel or profile). Pass exactly one of section or url."""
        if (section is None) == (url is None):
            raise ValueError("pass exactly one of section or url")
        driver = await rt.uia()
        if url is not None:
            return dict(await driver.navigate_url(url))
        return dict(await driver.navigate_section(section or "home"))

    @tool(server, annotations=WRITE)
    async def app_click(ref: str | None = None, name: str | None = None,
                        role: str | None = None, confirm: bool = False) -> dict[str, Any]:
        """Click an element in the Instagram app by its [eN] ref from app_snapshot, or by
        exact accessible name (+ optional role such as "button", "hyperlink").

        Safe clicks (open a post, a profile, a tab, "More", "Close") just happen. Controls
        that change the account (Like, Follow, Save, Comment, Send, Post, Share, Delete,
        Unfollow...) are refused and return a dry run unless confirm=true — set it ONLY
        after the user explicitly said yes in chat. With confirm=true, Like/Unlike/Save/
        Remove(unsave)/Follow are pressed through the driver's rate-limited write path."""
        if ref is None and name is None:
            raise ValueError("pass ref or name")
        driver = await rt.uia()
        node = None
        if ref is not None:
            snap = _last_snapshot(driver)
            node = snap.get(ref) if snap is not None else None
        label = node.name if node is not None else (name or "")
        risky = bool(WRITE_VERBS.search(label)) or bool(name and WRITE_VERBS.search(name))
        if risky and not confirm:
            return _dry_run(f"click {label or ref!r} in the Instagram app",
                            "this control can change the user's Instagram account")
        if confirm and risky:
            if node is None:
                snap = await driver.take_snapshot()
                found = snap.find(name, role, visible=True) or snap.find(name, role)
                if not found:
                    raise ElementNotFoundError(f"no element named {name!r}")
                node = found[0]
            method = _WRITE_METHODS.get(node.name)
            if method is not None:
                result = await getattr(driver, method)(node.ref, confirm=True)
                return {"performed": True, "action": method, **dict(result)}
            from heliograph.drivers.uia.writes import is_write_control

            if is_write_control(node):
                raise UnsafeActionError(
                    f"{node.name!r} has no confirmed click handler in the live app",
                    hint="Use the ig_* write tools (ig_comment, ig_send_dm, ig_unfollow...).")
            return {"performed": True, **dict(await driver.click(node.ref))}
        return {"performed": True, **dict(await driver.click(ref, name=name, role=role))}

    @tool(server, annotations=READ_ONLY)
    async def app_scroll(
        direction: Literal["up", "down"] = "down", pages: int = 1
    ) -> dict[str, Any]:
        """Scroll the Instagram app by `pages` screens (in the Reels viewer one page = one
        reel). Take a new app_snapshot or app_visible_posts afterwards."""
        driver = await rt.uia()
        method = await driver.scroll(direction, max(1, min(pages, 10)))
        return {"scrolled": direction, "pages": pages, "method": method}

    @tool(server, annotations=WRITE)
    async def app_type(text: str, ref: str | None = None, name: str | None = None,
                       submit: bool = False, confirm: bool = False) -> dict[str, Any]:
        """Type text into a field of the Instagram app (by [eN] ref or field name, else the
        focused element), e.g. the search box. submit=true presses Enter, which can send a
        message or post a comment, so it requires confirm=true (only after the user said
        yes in chat); without it a dry run is returned."""
        if submit and not confirm:
            return _dry_run(f"type {text!r} and press Enter in the Instagram app",
                            "pressing Enter can send a message or post a comment")
        driver = await rt.uia()
        await driver.type_text(text, ref, name=name, submit=submit)
        return {"performed": True, "typed_chars": len(text), "submitted": submit}

    @tool(server, annotations=READ_ONLY)
    async def app_visible_posts(visible_only: bool = True) -> dict[str, Any]:
        """Posts/reels currently on screen in the Instagram app: author, shortcode/URL,
        caption, like/comment counts, liked/saved state and the refs of their action
        buttons (usable with app_click after user confirmation)."""
        driver = await rt.uia()
        posts = await driver.visible_posts(visible_only=visible_only)
        return {"count": len(posts), "posts": posts}

    @tool(server, annotations=READ_ONLY)
    async def app_badges() -> dict[str, Any]:
        """Unread counts shown in the app's navigation (messages, notifications) and the
        section currently open."""
        driver = await rt.uia()
        return {"badges": await driver.unread_badges(),
                "section": await driver.current_section()}
