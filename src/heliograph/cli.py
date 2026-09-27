"""Heliograph command-line interface (``heliograph ...``)."""

from __future__ import annotations

import json
import sys
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from heliograph import __version__, eye
from heliograph.detect import EnvironmentReport, detect_environment

app = typer.Typer(
    name="heliograph",
    help="Heliograph — link Claude to Instagram on your own device.",
    no_args_is_help=True,
    add_completion=False,
)
eye_app = typer.Typer(help="The background eye: live tail and health report.")
app.add_typer(eye_app, name="eye")

console = Console()


def _utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure) and (stream.encoding or "").lower() not in ("utf-8", "utf8"):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


@app.callback()
def _main() -> None:
    """Heliograph — link Claude to Instagram on your own device."""
    _utf8_stdio()
    eye.install_excepthook()


@app.command()
def version() -> None:
    """Print the Heliograph version."""
    console.print(f"heliograph {__version__}")


def _ffmpeg_hint(os_name: str) -> str:
    return {
        "windows": "winget install Gyan.FFmpeg",
        "macos": "brew install ffmpeg",
    }.get(os_name, "sudo apt install ffmpeg (or your distro's package)")


def _doctor_rows(r: EnvironmentReport) -> list[tuple[bool | None, str, str, str]]:
    """(status, check, detail, hint) rows; status None = not applicable / informational."""
    app_ = r.instagram_app
    rows: list[tuple[bool | None, str, str, str]] = [
        (True, "OS", f"{r.os} — {r.os_version}", ""),
        (True, "Python", r.python_version, ""),
    ]
    if app_.supported:
        rows.append((app_.installed, "Instagram Store app",
                     f"{app_.name} {app_.version} ({app_.aumid})" if app_.installed
                     else (app_.reason or "not installed"),
                     "" if app_.installed else "heliograph setup  (opens the Microsoft Store)"))
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
                     "" if tool.found else _ffmpeg_hint(r.os)))
    rows.append((r.browser_profile_initialized, "CDP browser profile", r.browser_profile_dir,
                 "" if r.browser_profile_initialized else "heliograph login  (one-time sign-in)"))
    return rows


@app.command()
def doctor(
    as_json: Annotated[bool, typer.Option("--json", help="Print the raw report as JSON.")] = False,
) -> None:
    """Check this machine and explain how to fix anything missing."""
    report = detect_environment()
    if as_json:
        typer.echo(json.dumps(report.model_dump(mode="json"), indent=2))
    else:
        table = Table(title=f"Heliograph {__version__} — doctor", show_lines=False)
        table.add_column("", width=2)
        table.add_column("check", style="bold")
        table.add_column("detail", overflow="fold")
        table.add_column("fix", style="yellow", overflow="fold")
        for status, check, detail, hint in _doctor_rows(report):
            mark = {True: "[green]✓[/]", False: "[red]✗[/]", None: "[dim]–[/]"}[status]
            table.add_row(mark, check, detail, hint)
        console.print(table)
        if report.ok:
            console.print("[green]Ready.[/] Nothing critical is missing.")
        else:
            console.print(f"[red]Critical missing:[/] {', '.join(report.critical_missing)}")
    if not report.ok:
        raise typer.Exit(code=1)


@eye_app.callback(invoke_without_command=True)
def eye_tail(
    ctx: typer.Context,
    lines: Annotated[int, typer.Option("--lines", "-n", help="Rows to show.")] = 25,
    no_follow: Annotated[bool, typer.Option("--no-follow", help="Print once and exit.")] = False,
) -> None:
    """Live tail of eye events with rolling health (Ctrl+C to stop)."""
    if ctx.invoked_subcommand is None:
        eye.tail(follow=not no_follow, lines=lines, console=console)


@eye_app.command("report")
def eye_report(
    hours: Annotated[float, typer.Option(help="Look-back window in hours.")] = 24,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """Summarise recent errors, latency and anomalies."""
    rep = eye.report(window_seconds=hours * 3600)
    if as_json:
        typer.echo(rep.model_dump_json(indent=2))
        return
    style = "red" if rep.error_rate > 0.1 else "green"
    console.print(f"[bold]Last {hours:g}h:[/] {rep.total} spans, "
                  f"[{style}]{rep.errors} errors ({rep.error_rate:.1%})[/]")
    ops = Table(title="Operations")
    for col in ("name", "count", "errors", "p50 ms", "p95 ms", "max ms"):
        ops.add_column(col, justify="left" if col == "name" else "right")
    for op in rep.ops[:20]:
        ops.add_row(op.name, str(op.count), str(op.errors), f"{op.p50_ms or 0:.0f}",
                    f"{op.p95_ms or 0:.0f}", f"{op.max_ms or 0:.0f}")
    console.print(ops)
    for a in rep.anomalies:
        console.print(f"[yellow]anomaly[/] {a.kind} {a.name}: {a.detail} (×{a.count})")
    for e in rep.recent_errors:
        console.print(f"[red]{e.ts}[/] {e.name} {e.error_type}: {e.message} "
                      f"[dim]trace={(e.trace_id or '')[:8]}[/]")


def _not_implemented(command: str) -> None:
    console.print(f"[yellow]`heliograph {command}` is not yet implemented.[/]")
    raise typer.Exit(code=2)


# --- Stubs: to be implemented by the driver / media / MCP engineers. -----------------------


@app.command()
def setup() -> None:
    """[stub] One-time setup: install the Instagram app, browser profile, models."""
    _not_implemented("setup")


@app.command()
def login() -> None:
    """[stub] Open the dedicated browser profile so you can sign in to Instagram once."""
    _not_implemented("login")


@app.command()
def mcp() -> None:
    """[stub] Run the Heliograph MCP server (stdio) for Claude."""
    _not_implemented("mcp")


@app.command()
def extract(url: Annotated[str, typer.Argument(help="Reel/post URL.")]) -> None:
    """[stub] Build a dossier (video, transcript, keyframes) for a reel or post."""
    _not_implemented("extract")


if __name__ == "__main__":  # pragma: no cover
    app()
