"""Rendering of the environment report shared by ``doctor`` and ``setup``."""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

from heliograph import __version__
from heliograph.detect import EnvironmentReport
from heliograph.detect.models import NOT_INSTALLED

__all__ = ["doctor_rows", "ffmpeg_hint", "render_report"]


def ffmpeg_hint(os_name: str) -> str:
    """Install command for ffmpeg on ``os_name``."""
    return {
        "windows": "winget install Gyan.FFmpeg",
        "macos": "brew install ffmpeg",
    }.get(os_name, "sudo apt install ffmpeg (or your distro's package)")


def doctor_rows(r: EnvironmentReport) -> list[tuple[bool | None, str, str, str]]:
    """(status, check, detail, hint) rows; status None = not applicable / informational."""
    app_ = r.instagram_app
    rows: list[tuple[bool | None, str, str, str]] = [
        (True, "OS", f"{r.os} — {r.os_version}", ""),
        (True, "Python", r.python_version, ""),
    ]
    if app_.supported:
        app_hint = ""
        if not app_.installed:  # only point at the Store when the app is truly absent
            app_hint = ("uv run heliograph setup  (opens the Microsoft Store)"
                        if app_.reason == NOT_INSTALLED
                        else "check failed; re-run: uv run heliograph doctor")
        rows.append((app_.installed, "Instagram Store app",
                     f"{app_.name} {app_.version} ({app_.aumid})" if app_.installed
                     else (app_.reason or NOT_INSTALLED), app_hint))
        if app_.installed:
            rows.append((app_.window_open, "Instagram window open",
                         app_.window_title or (app_.reason or "no window found"),
                         "" if app_.window_open else "optional: open Instagram from Start"))
    else:
        rows.append((None, "Instagram Store app", app_.reason or "n/a", ""))
    best = r.preferred_browser
    rows.append((best is not None, "Browser (Edge/Chrome)",
                 ", ".join(f"{b.channel}: {b.path}" for b in r.browsers) or "none found",
                 "" if best else "install Microsoft Edge or Google Chrome"))
    rows.append((r.playwright.importable, "playwright",
                 r.playwright.version or (r.playwright.reason or ""),
                 "" if r.playwright.importable else "uv sync"))
    if r.os == "windows":
        rows.append((r.uiautomation.importable, "uiautomation",
                     r.uiautomation.version or (r.uiautomation.reason or ""),
                     "" if r.uiautomation.importable else "uv sync"))
    for tool in (r.ffmpeg, r.ffprobe):
        rows.append((tool.found, tool.name, tool.version or (tool.reason or ""),
                     "" if tool.found else ffmpeg_hint(r.os)))
    profile_detail = r.browser_profile_dir
    if r.logged_in is not None:
        profile_detail += " (last seen logged in)" if r.logged_in else " (last seen logged out)"
    rows.append((not r.needs_login, "CDP browser profile", profile_detail,
                 "" if not r.needs_login else "uv run heliograph login  (one-time sign-in)"))
    return rows


def render_report(report: EnvironmentReport, console: Console, *, title: str = "doctor") -> None:
    """Print the doctor table."""
    table = Table(title=f"Heliograph {__version__} — {title}", show_lines=False)
    table.add_column("", width=2)
    table.add_column("check", style="bold")
    table.add_column("detail", overflow="fold")
    table.add_column("fix", style="yellow", overflow="fold")
    for status, check, detail, hint in doctor_rows(report):
        mark = {True: "[green]✓[/]", False: "[red]✗[/]", None: "[dim]–[/]"}[status]
        table.add_row(mark, check, detail, hint)
    console.print(table)
