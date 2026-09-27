"""``heliograph dossier show|frames``: read a built dossier from the terminal (CLI parity
with the ``ig_read_dossier`` / ``ig_view_frames`` MCP tools)."""

from __future__ import annotations

import os
import sys
import webbrowser
from collections.abc import Sequence
from pathlib import Path

from rich.console import Console
from rich.markup import escape

__all__ = ["open_path", "run_frames", "run_show"]


def _folder(ref: str, root: Path | None) -> Path:
    from heliograph.config import get_settings
    from heliograph.mcp.dossiers import find_dossier

    return find_dossier(ref, root or get_settings().dossier_path)


def open_path(path: Path) -> None:
    """Open ``path`` with the system's default viewer."""
    startfile = getattr(os, "startfile", None)  # Windows only
    if sys.platform == "win32" and startfile is not None:
        startfile(path)
    else:
        webbrowser.open(path.resolve().as_uri())


def run_show(console: Console, ref: str, *, transcript: bool = False,
             root: Path | None = None) -> int:
    """Print ``dossier.md`` (and optionally ``transcript.md``) of the dossier ``ref``."""
    folder = _folder(ref, root)
    md = folder / "dossier.md"
    if not md.is_file():
        console.print(f"[red]No dossier.md in {folder}[/]")
        return 1
    # Plain output (no Rich markup/wrapping) so the text can be piped or copied as-is.
    console.out(md.read_text(encoding="utf-8"), highlight=False)
    if transcript and (folder / "transcript.md").is_file():
        console.out((folder / "transcript.md").read_text(encoding="utf-8"), highlight=False)
    return 0


def run_frames(console: Console, ref: str, *, frames: Sequence[int] = (),
               crop: str | None = None, scale: float | None = None, ocr: bool = False,
               open_files: bool = False, root: Path | None = None) -> int:
    """List keyframes (timestamp, path, OCR text), or write zoomed crops with ``crop``."""
    from heliograph.extract.zoom import crop_frame
    from heliograph.mcp.dossiers import load_frames

    folder = _folder(ref, root)
    opened: list[Path] = []
    if crop:
        if not frames:
            console.print("[red]--crop needs at least one --frame N[/]")
            return 2
        for i in frames:
            c = crop_frame(folder, i, crop, scale=scale, ocr=ocr)
            console.print(f"#{i} crop {c.box} x{c.scale} {c.size[0]}x{c.size[1]}: {c.path}",
                          soft_wrap=True)
            if c.ocr_text is not None:
                console.print(f"   OCR: {escape(c.ocr_text or '(no text)')}")
            opened.append(c.path)
    else:
        index = load_frames(folder)
        sheet = folder / "contact_sheet.jpg"
        if sheet.is_file():
            console.print(f"contact sheet: {sheet}", soft_wrap=True)
            if not frames:
                opened.append(sheet)
        wanted = set(frames)
        for f in index:
            if wanted and f["index"] not in wanted:
                continue
            console.print(f"#{f['index']:<3} {f['timestamp']}  {f['path']}", soft_wrap=True)
            if f.get("text"):
                console.print(f"     [dim]on screen:[/] {escape(str(f['text'])[:300])}")
            if wanted:
                opened.append(Path(f["path"]))
        missing = wanted - {f["index"] for f in index}
        if missing:
            console.print(f"[red]No frame(s) {sorted(missing)}[/]")
            return 1
        if not index:
            console.print("[yellow]No keyframes (photo post or frames not extracted).[/]")
    if open_files:
        for p in opened:
            open_path(p)
    return 0
