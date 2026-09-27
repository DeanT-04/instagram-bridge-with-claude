"""Status tools: environment, login state, and what the user still has to set up."""

from __future__ import annotations

import asyncio
from typing import Any

from mcp.server.fastmcp import FastMCP

from heliograph import __version__
from heliograph.mcp.common import LOCAL, tool
from heliograph.mcp.runtime import Runtime

__all__ = ["STORE_WEB_URL", "register", "setup_steps"]

STORE_WEB_URL = "https://apps.microsoft.com/detail/9NBLGGH5L9XT"
_FFMPEG_HINT = {"windows": "winget install Gyan.FFmpeg", "macos": "brew install ffmpeg"}


def setup_steps(report: Any, *, logged_in: bool | None) -> list[dict[str, str]]:
    """Ordered to-do list for the user derived from an EnvironmentReport."""
    steps: list[dict[str, str]] = []
    if not report.browsers:
        steps.append({"item": "browser", "action": "Install Microsoft Edge or Google Chrome.",
                      "why": "The deep (ig_*) tools drive a dedicated browser profile."})
    if not report.playwright.importable:
        steps.append({"item": "playwright", "action": "Run `uv sync` in the Heliograph folder."})
    if not report.ffmpeg.found or not report.ffprobe.found:
        steps.append({"item": "ffmpeg",
                      "action": _FFMPEG_HINT.get(report.os, "sudo apt install ffmpeg"),
                      "why": "Needed for keyframes and transcripts (ig_extract_*)."})
    if not report.browser_profile_initialized or logged_in is False:
        steps.append({"item": "login",
                      "action": "Run `uv run heliograph login` in a terminal and sign in to "
                                "Instagram yourself in the window that opens (one time).",
                      "why": "Heliograph never sees your password; it reuses that session."})
    app = report.instagram_app
    if app.supported and not app.installed:
        steps.append({"item": "instagram_app",
                      "action": f"Optional: install the Instagram app from the Microsoft Store "
                                f"({STORE_WEB_URL}). Call heliograph_setup_check with "
                                "open_store=true to open the Store page for the user.",
                      "why": "Enables the app_* live-app tools (your real app window)."})
    return steps


def register(server: FastMCP, rt: Runtime) -> None:
    """Register the status tools."""

    async def _login_state() -> tuple[bool | None, str]:
        driver = rt.cdp_driver()
        try:
            running = await asyncio.to_thread(driver.launcher.find_running)
        except Exception as exc:
            return None, f"could not check the browser: {exc}"
        if running is None and not await rt.cdp_connected():
            return None, "dedicated browser not running (starts on the first ig_* call)"
        try:
            await driver.connect()
            ok = await driver.is_logged_in()
        except Exception as exc:
            return None, f"could not attach to the browser: {exc}"
        return ok, "logged in" if ok else "browser open but not logged in"

    @tool(server, annotations=LOCAL)
    async def heliograph_status() -> dict[str, Any]:
        """Show Heliograph's health: OS, Instagram Store app, browsers, ffmpeg, login state
        of the dedicated browser profile, and which drivers are usable right now.

        Call this first when something fails or at the start of a session. It never
        launches anything; login is only checked if the Heliograph browser is already open.
        Drivers: "cdp" powers all ig_* tools (any OS); "uia" powers app_* tools (Windows +
        Microsoft Store Instagram app).
        """
        from heliograph.detect import detect_environment

        report = await asyncio.to_thread(detect_environment)
        logged_in, login_detail = await _login_state()
        app = report.instagram_app
        return {
            "version": __version__,
            "os": report.os,
            "ready": report.ok,
            "critical_missing": report.critical_missing,
            "login": {"logged_in": logged_in, "detail": login_detail,
                      "profile_initialized": report.browser_profile_initialized},
            "drivers": {
                "cdp": {"available": bool(report.browsers) and report.playwright.importable,
                        "browser": report.preferred_browser.channel
                        if report.preferred_browser else None},
                "uia": {"available": report.os == "windows" and app.installed
                        and report.uiautomation.importable,
                        "app_installed": app.installed, "window_open": app.window_open},
            },
            "ffmpeg": report.ffmpeg.found and report.ffprobe.found,
            "environment": report.model_dump(mode="json", exclude_none=True),
        }

    @tool(server, annotations=LOCAL)
    async def heliograph_setup_check(open_store: bool = False) -> dict[str, Any]:
        """List what the user still needs to do before every Heliograph tool works, in
        order, with the exact command or link for each step.

        On Windows without the Instagram Store app this includes the Store link. Only set
        open_store=true after the user has asked you to open the Microsoft Store page;
        otherwise just show them the link. Nothing is installed automatically.
        """
        from heliograph.detect import detect_environment, install_instagram_app

        report = await asyncio.to_thread(detect_environment, check_window=False)
        logged_in, _ = await _login_state()
        steps = setup_steps(report, logged_in=logged_in)
        out: dict[str, Any] = {"done": not steps, "steps": steps}
        if open_store:
            if report.instagram_app.supported and not report.instagram_app.installed:
                out["store"] = await asyncio.to_thread(install_instagram_app)
            else:
                out["store"] = "Not opened: the Instagram app is installed or not supported."
        if not steps:
            out["message"] = "Everything is set up."
        return out
