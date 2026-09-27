"""Extraction tools: reel/post -> dossier (video, keyframes, transcript) and viewing it."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP, Image

from heliograph import eye
from heliograph.mcp.common import EXTRACT, LOCAL, clip, tool
from heliograph.mcp.dossiers import (
    find_dossier,
    is_dossier_image,
    load_frames,
    summarize_dossier,
)
from heliograph.mcp.runtime import Runtime

__all__ = ["register"]

MAX_IMAGES = 12


def _evenly(n: int, k: int) -> list[int]:
    if k >= n:
        return list(range(n))
    return sorted({round(i * (n - 1) / max(1, k - 1)) for i in range(k)})


async def _crops(folder: Path, frames: list[int] | None, crop: str, scale: float | None,
                 ocr: bool, cap: int) -> list[Any]:
    import asyncio

    from heliograph.extract.zoom import crop_frame

    if not frames:
        raise ValueError("crop needs `frames` (frame indices to zoom into)")
    content: list[Any] = []
    labels: list[str] = []
    for i in frames[:cap]:
        c = await asyncio.to_thread(crop_frame, folder, i, crop, scale=scale, ocr=ocr)
        label = f"frame #{i} crop {c.box} x{c.scale} -> {c.path.name}"
        if c.ocr_text is not None:
            label += f" [OCR: {c.ocr_text or '(no text)'}]"
        labels.append(label)
        content.append(Image(path=c.path))
    return [f"Dossier {folder.name}: " + "; ".join(labels), *content]


def register(server: FastMCP, rt: Runtime) -> None:
    """Register the extraction tools."""

    def root() -> Path:
        return rt.settings.dossier_path

    async def build(media: Any, *, transcript: bool, frames: bool, force: bool) -> Any:
        from heliograph.extract.dossier import abuild_dossier

        return await abuild_dossier(media, root(), transcript=transcript, frames=frames,
                                    force=force, whisper_model=rt.settings.whisper_model)

    @tool(server, annotations=EXTRACT, untrusted=True)
    async def ig_extract_media(ref: str, transcript: bool = True, frames: bool = True,
                               force: bool = False, md_chars: int = 20000) -> dict[str, Any]:
        """Build (or reuse) a dossier for one post/reel: downloads the video or images from
        Instagram's CDN, extracts de-duplicated native-resolution keyframes + a contact
        sheet, OCR of each keyframe's on-screen text (if an OCR engine is installed), and a
        timestamped speech transcript (faster-whisper, runs locally; non-English speech is
        re-run with a bigger model and also translated to English). dossier.md flags a
        possible caption/content mismatch and lists detected tickers/timeframes/indicators.

        `ref` = post/reel URL, shortcode or pk. Returns the dossier folder, the full
        dossier.md text (caption + merged speech/keyframe timeline, trimmed to md_chars),
        the frame list with timestamps and the contact_sheet path. Next: call
        ig_view_frames to actually look at the frames. Existing outputs are reused unless
        force=true. The first transcript may take minutes while the Whisper model downloads.
        """
        media = await (await rt.service()).media(ref)
        d = await build(media, transcript=transcript, frames=frames, force=force)
        out = summarize_dossier(d.root, md_chars=md_chars)
        out.update(code=media.code, owner=media.owner.username if media.owner else None,
                   skipped_stages=d.skipped, notes=d.notes, timings_s=d.timings)
        return out

    @tool(server, annotations=EXTRACT, untrusted=True)
    async def ig_extract_collection(collection: str, limit: int = 10,
                                    skip_existing: bool = True, transcript: bool = True,
                                    force: bool = False) -> dict[str, Any]:
        """Build dossiers for the first `limit` items of a saved collection (name such as
        "Trading strats", or numeric id), one after another. Progress is logged to the eye
        (see eye_recent with name "mcp.extract%").

        Items whose dossier.md already exists are skipped when skip_existing=true, so the
        call is resumable: prefer small limits (5-10) and call again. One failing item does
        not stop the rest; each result has status built | skipped | error. Returns a
        summary list with dossier paths; read each with ig_read_dossier."""
        from heliograph.extract.dossier import dossier_dir

        svc = await rt.service()
        results: list[dict[str, Any]] = []
        n = max(1, min(limit, 200))
        i = 0
        async for media in svc.iter_collection(collection, n):
            i += 1
            item: dict[str, Any] = {"n": i, "code": media.code, "url": media.url,
                                    "owner": media.owner.username if media.owner else None,
                                    "caption": clip(media.caption, 120)}
            folder = dossier_dir(media, root())
            if skip_existing and not force and (folder / "dossier.md").is_file():
                item.update(status="skipped", dossier_dir=str(folder))
            else:
                try:
                    d = await build(media, transcript=transcript, frames=True, force=force)
                    item.update(status="built", dossier_dir=str(d.root),
                                frames=len(d.frames), notes=d.notes or None)
                except Exception as exc:
                    item.update(status="error", error=f"{type(exc).__name__}: {exc}"[:300])
            eye.event("mcp.extract_collection.progress", n=i, code=media.code,
                      status=item["status"], error=item["status"] == "error")
            results.append({k: v for k, v in item.items() if v is not None})
        counts = {s: sum(r["status"] == s for r in results) for s in ("built", "skipped", "error")}
        return {"collection": collection, "processed": len(results), **counts,
                "dossier_root": str(root()), "items": results}

    @tool(server, annotations=LOCAL, untrusted=True)
    async def ig_read_dossier(dossier: str, md_chars: int = 30000,
                              include_transcript: bool = False) -> dict[str, Any]:
        """Read an existing dossier: its dossier.md text (metadata, caption, merged
        speech/keyframe timeline), the frame list with timestamps and file paths.
        `dossier` = shortcode, post URL, or a folder/dossier.md path inside the dossier
        root. Set include_transcript=true to also get transcript.md."""
        folder = find_dossier(dossier, root())
        out = summarize_dossier(folder, md_chars=md_chars)
        if include_transcript and (folder / "transcript.md").is_file():
            out["transcript_md_text"] = (folder / "transcript.md").read_text(encoding="utf-8")
        return out

    @tool(server, annotations=LOCAL, untrusted=True)
    async def ig_view_frames(dossier: str, frames: list[int] | None = None,
                             contact_sheet: bool = True, max_images: int = 6,
                             crop: str | None = None, scale: float | None = None,
                             ocr: bool = False) -> list[Any]:
        """Show dossier images so you can SEE them: the contact sheet (every keyframe tiled
        and labelled `#n mm:ss`) and/or specific keyframes by their frame index (from
        the frame list / dossier.md). Frames are native resolution; use single frames to
        read on-screen text, chart axes and indicator settings.

        Zoom: pass `crop` with `frames` to see only a region, upscaled so tiny chart
        labels become legible. `crop` = a preset (top, bottom, left, right, center,
        top-left, top-right, bottom-left, bottom-right, top-third, middle-third,
        bottom-third) or "x0,y0,x1,y1" as fractions of the frame (e.g. "0.6,0.3,1,0.45"
        for the right-hand price axis) or pixels. `scale` forces the zoom factor
        (default: up to 4x, ~1600 px). `ocr=true` also returns machine OCR of each crop.
        Crops are saved under <dossier>/crops/.

        `dossier` = shortcode, post URL or dossier path. With no `frames` and no contact
        sheet (e.g. photo posts), up to max_images frames/images spread evenly are shown.
        """
        folder = find_dossier(dossier, root())
        cap = max(1, min(max_images, MAX_IMAGES))
        if crop:
            return await _crops(folder, frames, crop, scale, ocr, cap)
        index = load_frames(folder)
        paths: list[tuple[str, Path]] = []
        sheet = folder / "contact_sheet.jpg"
        if contact_sheet and sheet.is_file():
            paths.append(("contact sheet", sheet))
        if frames:
            by_index = {f["index"]: f for f in index}
            missing = [i for i in frames if i not in by_index]
            if missing:
                raise ValueError(f"No frame(s) {missing}; available: "
                                 f"{sorted(by_index)[:50]}")
            paths += [(f"frame #{i} at {by_index[i]['timestamp']}", Path(by_index[i]["path"]))
                      for i in frames]
        elif not paths:
            if index:
                paths += [(f"frame #{index[j]['index']} at {index[j]['timestamp']}",
                           Path(index[j]["path"])) for j in _evenly(len(index), cap)]
            else:
                imgs = sorted((folder / "images").glob("*")) if (folder / "images").is_dir() \
                    else []
                paths += [(f"image {p.name}", p) for p in imgs[:cap]]
        # only real image files inside the dossier (no symlink/junction/tampered-index escape)
        paths = [(label, p) for label, p in paths if is_dossier_image(p, folder)][:cap]
        if not paths:
            raise ValueError(f"No images found in dossier {folder}")
        content: list[Any] = [f"Dossier {folder.name}: showing " +
                              "; ".join(label for label, _ in paths)]
        content += [Image(path=p) for _, p in paths]
        return content
