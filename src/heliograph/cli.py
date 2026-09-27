"""Heliograph command-line interface (``heliograph ...``)."""

from __future__ import annotations

import json
import sys
from typing import Annotated, NoReturn

import typer
from rich.console import Console
from rich.table import Table

from heliograph import __version__, eye
from heliograph.commands.doctor import render_report
from heliograph.detect import detect_environment
from heliograph.errors import HeliographError

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


@app.command()
def doctor(
    as_json: Annotated[bool, typer.Option("--json", help="Print the raw report as JSON.")] = False,
) -> None:
    """Check this machine and explain how to fix anything missing."""
    report = detect_environment()
    if as_json:
        data = report.model_dump(mode="json")
        data["needs_login"] = report.needs_login  # used by scripts/setup.*
        typer.echo(json.dumps(data, indent=2))
    else:
        render_report(report, console)
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


def _fail(exc: HeliographError) -> NoReturn:
    console.print(f"[red]{type(exc).__name__}:[/] {exc.message or exc}")
    if exc.hint:
        console.print(f"[yellow]Hint:[/] {exc.hint}")
    raise typer.Exit(code=1)


Account = Annotated[str, typer.Option("--account", help="Browser-profile account key.")]


@app.command()
def setup(
    with_whisper: Annotated[bool, typer.Option(
        "--with-whisper", help="Also pre-download the Whisper speech model.")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Answer yes to every prompt.")] = False,
    no_input: Annotated[bool, typer.Option(
        "--no-input", help="Never prompt (answer no); for scripts/CI.")] = False,
) -> None:
    """One-time setup: check the machine, explain fixes, offer the Store app and models."""
    from heliograph.commands.setup import run_setup

    raise typer.Exit(code=run_setup(console, with_whisper=with_whisper, assume_yes=yes,
                                    no_input=no_input))


@app.command()
def login(
    account: Account = "default",
    timeout: Annotated[float, typer.Option(help="Seconds to wait for you to sign in.")] = 300,
) -> None:
    """Open the dedicated browser profile so you can sign in to Instagram once, by hand."""
    from heliograph.commands.login import run_login

    try:
        ok = run_login(console, account=account, timeout=timeout)
    except HeliographError as exc:
        _fail(exc)
    if not ok:
        raise typer.Exit(code=1)


@app.command()
def mcp(account: Account = "default") -> None:
    """Run the Heliograph MCP server over stdio (Claude Code starts this for you)."""
    from heliograph.mcp import run_stdio

    run_stdio(account)


@app.command()
def extract(
    ref: Annotated[str | None, typer.Argument(
        help="Reel/post URL or shortcode.", show_default=False)] = None,
    collection: Annotated[str | None, typer.Option(
        "--collection", "-c", help='Saved collection name or id, e.g. "Trading strats".')] = None,
    limit: Annotated[int, typer.Option(help="Max items from the collection.")] = 20,
    no_transcript: Annotated[bool, typer.Option(
        "--no-transcript", help="Skip speech-to-text.")] = False,
    force: Annotated[bool, typer.Option(
        "--force", help="Rebuild every stage even if outputs exist.")] = False,
    account: Account = "default",
) -> None:
    """Build dossiers (video, keyframes, transcript) for a reel/post or a whole collection."""
    from heliograph.commands.extract import run_extract

    try:
        code = run_extract(console, ref=ref, collection=collection, limit=limit,
                           transcript=not no_transcript, force=force, account=account)
    except HeliographError as exc:
        _fail(exc)
    raise typer.Exit(code=code)


dossier_app = typer.Typer(help="Read built dossiers: dossier.md, keyframes, zoomed crops.")
app.add_typer(dossier_app, name="dossier")
DossierRef = Annotated[str, typer.Argument(help="Shortcode, post URL or dossier folder.")]


@dossier_app.command("show")
def dossier_show(
    ref: DossierRef,
    transcript: Annotated[bool, typer.Option(
        "--transcript", help="Also print transcript.md.")] = False,
) -> None:
    """Print a dossier's dossier.md (caption, detected terms, speech/frame/OCR timeline)."""
    from heliograph.commands.dossier import run_show

    try:
        raise typer.Exit(code=run_show(console, ref, transcript=transcript))
    except HeliographError as exc:
        _fail(exc)


@dossier_app.command("frames")
def dossier_frames(
    ref: DossierRef,
    frame: Annotated[list[int] | None, typer.Option(
        "--frame", "-f", help="Frame index (repeatable).")] = None,
    crop: Annotated[str | None, typer.Option(
        help='Zoom region: preset (top, bottom-left, middle-third...) or "x0,y0,x1,y1" '
             "fractions/pixels.")] = None,
    scale: Annotated[float | None, typer.Option(help="Zoom factor (default: auto).")] = None,
    ocr: Annotated[bool, typer.Option("--ocr", help="OCR each crop.")] = False,
    open_files: Annotated[bool, typer.Option(
        "--open", help="Open the images in the default viewer.")] = False,
) -> None:
    """List keyframes with timestamps and on-screen text, or write zoomed crops (--crop)."""
    from heliograph.commands.dossier import run_frames

    try:
        code = run_frames(console, ref, frames=frame or [], crop=crop, scale=scale, ocr=ocr,
                          open_files=open_files)
    except (HeliographError, ValueError) as exc:
        if isinstance(exc, HeliographError):
            _fail(exc)
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from exc
    raise typer.Exit(code=code)


if __name__ == "__main__":  # pragma: no cover
    app()
