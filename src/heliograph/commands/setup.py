"""``heliograph setup``: check the machine, fix what can be fixed, explain the rest."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import typer
from rich.console import Console

from heliograph.commands.doctor import ffmpeg_hint, render_report
from heliograph.config import get_settings
from heliograph.detect import detect_environment, install_instagram_app
from heliograph.detect.models import NOT_INSTALLED

__all__ = ["run_setup"]

STORE_WEB_URL = "https://apps.microsoft.com/detail/9NBLGGH5L9XT"


def _ask(question: str, *, assume_yes: bool, no_input: bool) -> bool:
    if assume_yes:
        return True
    if no_input:
        return False
    return typer.confirm(question, default=False)


def _install_playwright_chromium(console: Console) -> bool:
    cmd = [sys.executable, "-m", "playwright", "install", "chromium"]
    console.print(f"[dim]$ {' '.join(cmd)}[/]")
    return subprocess.run(cmd, check=False).returncode == 0


def _preload_whisper(console: Console, model: str) -> None:
    from heliograph.media.transcribe import get_model

    with console.status(f"Downloading the Whisper '{model}' model (one time)…"):
        get_model(model)
    console.print(f"[green]✓[/] Whisper model '{model}' ready")


def run_setup(
    console: Console, *, with_whisper: bool = False, assume_yes: bool = False,
    no_input: bool = False,
) -> int:
    """Run setup; returns the process exit code (0 ok, 1 critical requirement missing)."""
    settings = get_settings()
    settings.ensure_dir(settings.home)
    console.print("[bold]Heliograph setup[/] — checking this machine…")
    report = detect_environment()
    render_report(report, console, title="setup")

    if not report.browsers:
        console.print("[yellow]No Microsoft Edge or Google Chrome found.[/] Install one "
                      "(recommended), or let Playwright download its own Chromium (~150 MB).")
        if _ask("Download Playwright's Chromium now?", assume_yes=assume_yes,
                no_input=no_input) and _install_playwright_chromium(console):
            console.print("[green]✓[/] Chromium installed")
    else:
        console.print(f"[green]✓[/] Browser: {report.browsers[0].channel} "
                      "(no browser download needed)")

    if not (report.ffmpeg.found and report.ffprobe.found):
        console.print(f"[yellow]ffmpeg is missing[/] (needed for keyframes and transcripts). "
                      f"Install it with:  [bold]{ffmpeg_hint(report.os)}[/]")

    app = report.instagram_app
    if app.supported and not app.installed and app.reason != NOT_INSTALLED:
        # The check itself failed (PowerShell blocked, timeout...): the app may well be
        # installed, so don't send the user to the Store for it.
        console.print(f"[yellow]Could not check for the Instagram Store app[/] ({app.reason}). "
                      "Re-run [bold]uv run heliograph doctor[/] later; it is optional.")
    elif app.supported and not app.installed:
        console.print("[yellow]The Instagram app from the Microsoft Store is not installed.[/] "
                      f"It is optional (enables the live-app tools): {STORE_WEB_URL}")
        if _ask("Open the Microsoft Store page for Instagram now?", assume_yes=assume_yes,
                no_input=no_input):
            console.print(install_instagram_app())

    if with_whisper:
        try:
            _preload_whisper(console, settings.whisper_model)
        except Exception as exc:  # network, disk...
            console.print(f"[red]Could not download the Whisper model:[/] {exc}")

    mcp_json = Path.cwd() / ".mcp.json"
    console.print()
    console.print("[bold]Next steps[/]")
    step = 1
    if report.needs_login:
        console.print(f" {step}. [bold]uv run heliograph login[/]  — sign in to Instagram "
                      "yourself in the window that opens (Heliograph never sees your password).")
        step += 1
    where = "this folder" if mcp_json.is_file() else "the Heliograph folder"
    console.print(f" {step}. Open Claude Code in {where}:  [bold]claude[/]  and approve the "
                  "'heliograph' MCP server when asked.")
    if not report.ok:
        console.print(f"[red]Critical missing:[/] {', '.join(report.critical_missing)}")
        return 1
    return 0
