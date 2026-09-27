"""``heliograph extract``: build dossiers for one post/reel or a saved collection."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from rich.console import Console

__all__ = ["format_timings", "run_extract"]


def format_timings(timings: dict[str, float], skipped: list[str]) -> str:
    """One line of per-stage seconds, e.g. ``download 1.2s | frames 3.4s | ... | total``."""
    parts = [f"{k} {v:.1f}s" for k, v in timings.items() if k != "total"]
    if "total" in timings:
        parts.append(f"total {timings['total']:.1f}s")
    if skipped:
        parts.append("reused: " + ", ".join(skipped))
    return " | ".join(parts)


async def _service(account: str) -> tuple[Any, Any]:
    from heliograph.drivers.cdp import CdpDriver
    from heliograph.instagram.service import InstagramService

    driver = CdpDriver(account)
    return driver, await InstagramService.from_driver(driver)


async def _extract(
    console: Console, *, ref: str | None, collection: str | None, limit: int,
    transcript: bool, force: bool, account: str, service: Any = None,
) -> list[tuple[str, str]]:
    from heliograph.config import get_settings
    from heliograph.extract.dossier import abuild_dossier, dossier_dir

    settings = get_settings()
    root: Path = settings.dossier_path
    driver = None
    if service is None:
        driver, service = await _service(account)
    results: list[tuple[str, str]] = []
    try:
        if ref is not None:
            medias = [await service.media(ref)]
        else:
            assert collection is not None
            medias = [m async for m in service.iter_collection(collection, limit)]
            console.print(f"{len(medias)} item(s) in {collection!r}")
        for i, media in enumerate(medias, start=1):
            label = f"[{i}/{len(medias)}] {media.code}"
            if ref is None and not force and (dossier_dir(media, root) / "dossier.md").is_file():
                results.append(("skipped", str(dossier_dir(media, root))))
                console.print(f"{label} [dim]exists, skipped[/]")
                continue
            try:
                with console.status(f"{label} downloading, keyframes, transcript…"):
                    d = await abuild_dossier(media, root, transcript=transcript, force=force,
                                             whisper_model=settings.whisper_model)
                results.append(("built", str(d.markdown_path)))
                console.print(f"{label} [green]✓[/] {d.markdown_path}")
                timings = format_timings(getattr(d, "timings", {}), getattr(d, "skipped", []))
                if timings:
                    console.print(f"    [dim]{timings}[/]")
            except Exception as exc:
                results.append(("error", f"{media.code}: {exc}"))
                console.print(f"{label} [red]✗ {type(exc).__name__}: {exc}[/]")
    finally:
        if driver is not None:
            await driver.shutdown()  # closes the browser only if we opened it
    return results


def run_extract(
    console: Console, *, ref: str | None, collection: str | None, limit: int = 20,
    transcript: bool = True, force: bool = False, account: str = "default",
    service: Any = None,
) -> int:
    """Build the dossiers and print their paths; returns an exit code."""
    if (ref is None) == (collection is None):
        console.print("[red]Pass a URL/shortcode or --collection NAME (not both).[/]")
        return 2
    results = asyncio.run(_extract(console, ref=ref, collection=collection, limit=limit,
                                   transcript=transcript, force=force, account=account,
                                   service=service))
    errors = sum(1 for status, _ in results if status == "error")
    built = sum(1 for status, _ in results if status == "built")
    console.print(f"Done: {built} built, {len(results) - built - errors} skipped, {errors} failed.")
    return 1 if errors and not built else 0
