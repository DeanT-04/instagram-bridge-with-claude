"""Build a Claude-readable "dossier" folder for one Instagram post or reel.

Layout (``<out_root>/<owner_username>/<code>/``)::

    meta.json           Media.model_dump (the raw API payload is excluded)
    caption.md          the caption text
    video.mp4           the best video rendition (videos/reels)
    images/NN.jpg       still images (photos / carousel items)
    transcript.json     faster-whisper segments + detected language
    transcript.md       "[mm:ss] text" lines
    frames/*.jpg        deduplicated keyframes at native resolution (+ frames/frames.json)
    frames/ocr.json     on-screen text per keyframe (when an OCR engine is installed)
    crops/*.png         zoomed regions made on demand by :mod:`heliograph.extract.zoom`
    contact_sheet.jpg   all keyframes tiled with timestamps
    dossier.md          everything stitched together: metadata, caption, detected terms,
                        a possible-mismatch flag and a merged speech/keyframe/OCR
                        timeline with relative image paths

Every stage is idempotent: outputs that already exist are reused unless ``force=True``.
``meta.json``, ``caption.md`` and ``dossier.md`` are cheap and always rewritten.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from heliograph.extract.render import render_markdown
from heliograph.extract.signals import Signals, find_signals
from heliograph.eye import ActiveSpan, span
from heliograph.instagram.models import Media
from heliograph.media.download import DEFAULT_MAX_BYTES, download_sync
from heliograph.media.frames import (
    Frame,
    contact_sheet,
    extract_keyframes,
    load_index,
)
from heliograph.media.ocr import OcrResult, get_engine, load_ocr, ocr_frames
from heliograph.media.transcribe import Transcript, load_transcript, transcribe

__all__ = ["Dossier", "abuild_dossier", "build_dossier", "dossier_dir"]

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
_RESERVED = re.compile(r"^(con|prn|aux|nul|conin\$|conout\$|com[0-9]|lpt[0-9])$", re.IGNORECASE)
_EXT_BY_TYPE = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
                "image/heic": ".heic", "image/gif": ".gif"}


@dataclass
class Dossier:
    """Paths and results of :func:`build_dossier`."""

    root: Path
    media: Media
    meta_path: Path
    caption_path: Path
    markdown_path: Path
    video_path: Path | None = None
    video_sha256: str | None = None
    images: list[Path] = field(default_factory=list)
    transcript: Transcript | None = None
    frames: list[Frame] = field(default_factory=list)
    contact_sheet: Path | None = None
    ocr: OcrResult | None = None
    signals: Signals | None = None
    timings: dict[str, float] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _safe(part: str | None, fallback: str) -> str:
    cleaned = _UNSAFE.sub("_", part or "").strip("._")[:100].strip("._")
    if not cleaned:
        return fallback
    # Windows device names (CON, NUL, COM1, LPT1...) are reserved even with an extension;
    # Instagram usernames such as "con" or "aux.x" are legal, so prefix them.
    if _RESERVED.match(cleaned.split(".", 1)[0]):
        cleaned = f"_{cleaned}"
    return cleaned


def dossier_dir(media: Media, out_root: Path) -> Path:
    """Folder for ``media``: ``<out_root>/<owner_username>/<code or id>`` (sanitised)."""
    owner = _safe(media.owner.username if media.owner else None, "unknown")
    return out_root / owner / _safe(media.code or media.id, "media")


@contextmanager
def _stage(d: Dossier, name: str, **attrs: Any) -> Iterator[ActiveSpan]:
    t0 = time.perf_counter()
    with span(f"dossier.{name}", **attrs) as s:
        try:
            yield s
        finally:
            d.timings[name] = round(time.perf_counter() - t0, 3)


def _write_meta(d: Dossier) -> None:
    m = d.media
    d.meta_path.write_text(
        json.dumps(m.model_dump(mode="json"), indent=2, ensure_ascii=False), "utf-8"
    )
    d.caption_path.write_text((m.caption or "").rstrip() + "\n", "utf-8")


def _download_video(d: Dossier, *, force: bool, max_bytes: int) -> None:
    target = d.root / "video.mp4"
    if target.is_file() and not force:
        d.video_path = target
        d.skipped.append("download")
        return
    if not d.media.video_url:
        d.notes.append("media is a video but has no video_url; nothing downloaded")
        return
    with _stage(d, "download") as s:
        res = download_sync(d.media.video_url, target, max_bytes=max_bytes)
        d.video_path, d.video_sha256 = res.path, res.sha256
        s.set(bytes=res.bytes, sha256=res.sha256)


def _download_images(d: Dossier, *, force: bool, max_bytes: int) -> None:
    m = d.media
    sources = [c.images[0].url for c in m.children if c.images] or (
        [m.images[0].url] if m.images else []
    )
    if not sources:
        return
    img_dir = d.root / "images"
    with _stage(d, "images", count=len(sources)) as s:
        total = 0
        for i, url in enumerate(sources, start=1):
            existing = sorted(img_dir.glob(f"{i:02d}.*"))
            if existing and not force:
                d.images.append(existing[0])
                continue
            res = download_sync(url, img_dir / f"{i:02d}.bin", max_bytes=max_bytes)
            ctype = res.content_type.split(";")[0].strip().lower()
            final = res.path.with_suffix(_EXT_BY_TYPE.get(ctype, ".jpg"))
            res.path.replace(final)
            d.images.append(final)
            total += res.bytes
        s.set(bytes=total)


def _frames(d: Dossier, *, force: bool, max_frames: int) -> bool:
    """Extract (or reuse) keyframes; returns True if they were regenerated."""
    assert d.video_path is not None
    frames_dir = d.root / "frames"
    cached = None if force else load_index(frames_dir)
    if cached is not None:
        d.frames = cached
        d.skipped.append("frames")
        return False
    with _stage(d, "frames") as s:
        d.frames = extract_keyframes(d.video_path, frames_dir, max_frames=max_frames)
        s.set(frames=len(d.frames))
    return True


def _contact_sheet(d: Dossier, *, force: bool) -> None:
    target = d.root / "contact_sheet.jpg"
    if target.is_file() and not force:
        d.contact_sheet = target
        d.skipped.append("contact_sheet")
        return
    with _stage(d, "contact_sheet", frames=len(d.frames)):
        d.contact_sheet = contact_sheet(d.frames, target)


def _ocr(d: Dossier, *, force: bool, engine_name: str | None) -> None:
    frames_dir = d.root / "frames"
    cached = None if force else load_ocr(frames_dir)
    if cached is not None and {f.index for f in cached.frames} == {f.index for f in d.frames}:
        d.ocr = cached
        d.skipped.append("ocr")
        return
    engine = get_engine(engine_name)
    if engine is None:
        if engine_name != "off":
            d.notes.append("On-screen text was not OCR'd: no OCR engine installed "
                           "(`uv sync --extra ocr`, or `--extra ocr-windows` on Windows).")
        return
    with _stage(d, "ocr", engine=engine.name) as s:
        d.ocr = ocr_frames(d.frames, frames_dir, engine)
        s.set(chars=len(d.ocr.text))


def _signals(d: Dossier) -> None:
    speech = d.transcript.english_text if d.transcript else ""
    if d.transcript and d.transcript.translation:
        speech += "\n" + d.transcript.text
    d.signals = find_signals(d.media.caption or "", speech, d.ocr.text if d.ocr else "")


def _transcript(d: Dossier, *, force: bool, model_size: str | None) -> None:
    assert d.video_path is not None
    cached = None if force else load_transcript(d.root)
    if cached is not None:
        d.transcript = cached
        d.skipped.append("transcript")
        return
    with _stage(d, "transcript") as s:
        d.transcript = transcribe(d.video_path, d.root, model_size=model_size)
        s.set(language=d.transcript.language, segments=len(d.transcript.segments),
              model=d.transcript.model, translated=len(d.transcript.translation))
        d.timings.update({f"transcript.{k}": v for k, v in d.transcript.timings.items()})


def build_dossier(
    media: Media,
    out_root: Path,
    *,
    frames: bool = True,
    transcript: bool = True,
    ocr: bool = True,
    force: bool = False,
    whisper_model: str | None = None,
    ocr_engine: str | None = None,
    max_frames: int = 24,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> Dossier:
    """Create or refresh the dossier folder for ``media`` and return what it contains.

    Blocking (downloads run their own event loop); from async code use
    :func:`abuild_dossier`.

    Args:
        media: Parsed media (``Media.from_api``).
        out_root: Root directory; the dossier goes in ``<owner>/<code>/`` beneath it.
        frames: Extract keyframes and a contact sheet (videos only).
        transcript: Transcribe speech (videos only).
        ocr: OCR the keyframes' on-screen text (needs an OCR engine; skipped otherwise).
        force: Recompute every stage even when its outputs exist.
        whisper_model: Override ``Settings.whisper_model``.
        ocr_engine: Override ``Settings.ocr_engine`` (auto, rapidocr, windows, off).
        max_frames: Keyframe cap.
        max_bytes: Download size cap per file.
    """
    root = dossier_dir(media, out_root)
    root.mkdir(parents=True, exist_ok=True)
    d = Dossier(
        root=root,
        media=media,
        meta_path=root / "meta.json",
        caption_path=root / "caption.md",
        markdown_path=root / "dossier.md",
    )
    t0 = time.perf_counter()
    with span(
        "extract.dossier",
        code=media.code,
        owner=media.owner.username if media.owner else None,
        media_type=media.media_type.value,
        force=force,
    ) as s:
        _write_meta(d)
        if media.is_video:
            _download_video(d, force=force, max_bytes=max_bytes)
        else:
            _download_images(d, force=force, max_bytes=max_bytes)
        if d.video_path is not None:
            regenerated = False
            if frames:
                regenerated = _frames(d, force=force, max_frames=max_frames)
                _contact_sheet(d, force=force or regenerated)
                if ocr and d.frames:
                    _ocr(d, force=force or regenerated, engine_name=ocr_engine)
            if transcript:
                _transcript(d, force=force, model_size=whisper_model)
        with _stage(d, "markdown"):
            _signals(d)
            d.markdown_path.write_text(render_markdown(d), "utf-8")
        d.timings["total"] = round(time.perf_counter() - t0, 3)
        s.set(
            root=str(root),
            frames=len(d.frames),
            images=len(d.images),
            language=d.transcript.language if d.transcript else None,
            segments=len(d.transcript.segments) if d.transcript else None,
            ocr_engine=d.ocr.engine if d.ocr else None,
            mismatch=bool(d.signals and d.signals.mismatch),
            video_bytes=d.video_path.stat().st_size if d.video_path else None,
            skipped=d.skipped,
            timings=d.timings,
        )
    return d


async def abuild_dossier(media: Media, out_root: Path, **kwargs: Any) -> Dossier:
    """Async wrapper: runs :func:`build_dossier` in a worker thread."""
    return await asyncio.to_thread(build_dossier, media, out_root, **kwargs)
