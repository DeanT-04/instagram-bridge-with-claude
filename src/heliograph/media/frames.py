"""Keyframe extraction for Claude: scene changes + interval sampling, deduped and capped.

Pipeline:

1. One ffmpeg pass selects a frame when the scene score exceeds ``scene_threshold`` **or**
   at least ``min_interval`` seconds have passed since the previous selected frame (so
   slow text slides or talking-head segments are still sampled), always including the
   first frame. ``showinfo`` reports each selected frame's ``pts_time``.
2. Near-duplicates are dropped with a perceptual hash (compared with the previous kept
   frame, so the timeline stays intact).
3. If more than ``max_frames`` remain, an evenly spaced subset is kept.
4. Frames are JPEGs scaled to at most ``max_height`` px high; an index is written to
   ``frames.json`` next to them.

:func:`contact_sheet` tiles frames into one image with the timestamps burned in, which is
far cheaper for Claude to look at than two dozen separate images.
"""

from __future__ import annotations

import json
import math
import re
import shutil
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import imagehash
from PIL import Image, ImageDraw, ImageFont

from heliograph.eye import attach_artifact, span
from heliograph.media.ffmpeg import FFmpegError, input_args, output_path, run

__all__ = [
    "INDEX_NAME",
    "Frame",
    "contact_sheet",
    "extract_keyframes",
    "format_ts",
    "load_index",
    "pick_evenly",
]

INDEX_NAME = "frames.json"
_PTS_RE = re.compile(r"\bn:\s*\d+\b.*?\bpts_time:\s*(-?[\d.]+(?:e[-+]?\d+)?)", re.IGNORECASE)


@dataclass(frozen=True)
class Frame:
    """One extracted keyframe."""

    index: int
    time_s: float
    path: Path
    phash: str

    @property
    def timestamp(self) -> str:
        """``mm:ss`` label for :attr:`time_s`."""
        return format_ts(self.time_s)

    def to_dict(self, relative_to: Path | None = None) -> dict[str, Any]:
        """JSON-friendly dict; ``path`` is made relative to ``relative_to`` if given."""
        d = asdict(self)
        d["path"] = (
            self.path.relative_to(relative_to).as_posix() if relative_to else self.path.as_posix()
        )
        d["timestamp"] = self.timestamp
        return d


def format_ts(seconds: float) -> str:
    """Format seconds as ``mm:ss`` (``h:mm:ss`` past an hour)."""
    total = max(0, int(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def pick_evenly(n: int, k: int) -> list[int]:
    """Return ``min(n, k)`` sorted, evenly spaced indices into ``range(n)`` (ends included)."""
    if k <= 0 or n <= 0:
        return []
    if n <= k:
        return list(range(n))
    if k == 1:
        return [0]
    return sorted({round(i * (n - 1) / (k - 1)) for i in range(k)})


def _parse_pts(stderr: str) -> list[float]:
    return [
        float(m.group(1))
        for line in stderr.splitlines()
        if "Parsed_showinfo" in line and (m := _PTS_RE.search(line))
    ]


def _select_expr(scene_threshold: float, min_interval: float) -> str:
    return (
        f"select='gt(scene,{scene_threshold})+isnan(prev_selected_t)"
        f"+gte(t-prev_selected_t,{min_interval})'"
    )


def _dedupe(
    candidates: list[tuple[float, Path]], *, hash_size: int, max_distance: int
) -> list[tuple[float, Path, imagehash.ImageHash]]:
    kept: list[tuple[float, Path, imagehash.ImageHash]] = []
    for t, path in candidates:
        with Image.open(path) as img:
            h = imagehash.phash(img, hash_size=hash_size)
        if kept and (h - kept[-1][2]) <= max_distance:
            continue
        kept.append((t, path, h))
    return kept


def extract_keyframes(
    video: Path,
    out_dir: Path,
    *,
    scene_threshold: float = 0.3,
    min_interval: float = 2.0,
    max_frames: int = 24,
    max_height: int = 1080,
    jpeg_qscale: int = 5,
    hash_size: int = 16,
    max_distance: int = 22,
    timeout: float = 900,
) -> list[Frame]:
    """Extract deduplicated, timestamped keyframes from ``video`` into ``out_dir``.

    Args:
        video: Input video file.
        out_dir: Destination directory (existing ``frame_*.jpg`` files are replaced).
        scene_threshold: ffmpeg scene score (0-1) above which a cut is detected.
        min_interval: Maximum gap in seconds between sampled frames.
        max_frames: Upper bound on returned frames (evenly subsampled).
        max_height: Frames taller than this are downscaled (never upscaled).
        jpeg_qscale: ffmpeg MJPEG ``-q:v`` (2 = best/largest ... 31 = worst/smallest).
        hash_size: Perceptual-hash size (bits = ``hash_size**2``).
        max_distance: Hamming distance at or below which a frame counts as a duplicate
            of the previously kept frame.
        timeout: ffmpeg timeout in seconds.

    Returns:
        Frames in time order, with 1-based ``index``.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = out_dir / ".raw"
    shutil.rmtree(raw_dir, ignore_errors=True)
    raw_dir.mkdir()
    for old in out_dir.glob("frame_*.jpg"):
        old.unlink()
    vf = f"{_select_expr(scene_threshold, min_interval)},showinfo,scale=-2:'min({max_height},ih)'"
    with span("media.frames", video=str(video), max_frames=max_frames) as s:
        try:
            proc = run(
                "ffmpeg",
                [
                    "-hide_banner", "-nostdin", "-y", *input_args(video),
                    "-an", "-sn", "-dn", "-vf", vf, "-fps_mode", "vfr",
                    "-q:v", str(jpeg_qscale), output_path(raw_dir / "raw_%05d.jpg"),
                ],
                timeout=timeout,
                op="frames",
            )
            raw_files = sorted(raw_dir.glob("raw_*.jpg"))
            times = _parse_pts(proc.stderr)
            if len(times) != len(raw_files):
                s.set(pts_mismatch=f"{len(times)} pts vs {len(raw_files)} files")
                if not times:
                    raise FFmpegError("could not parse frame timestamps from ffmpeg showinfo")
            candidates = list(zip(times, raw_files, strict=False))
            kept = _dedupe(candidates, hash_size=hash_size, max_distance=max_distance)
            chosen = [kept[i] for i in pick_evenly(len(kept), max_frames)]
            frames: list[Frame] = []
            for i, (t, path, h) in enumerate(chosen, start=1):
                dest = out_dir / f"frame_{i:03d}_{round(t * 1000):07d}ms.jpg"
                path.replace(dest)
                frames.append(Frame(index=i, time_s=round(t, 3), path=dest, phash=str(h)))
        finally:
            shutil.rmtree(raw_dir, ignore_errors=True)
        (out_dir / INDEX_NAME).write_text(
            json.dumps([f.to_dict(out_dir) for f in frames], indent=2), encoding="utf-8"
        )
        s.set(
            candidates=len(candidates),
            after_dedupe=len(kept),
            frames=len(frames),
            total_kb=round(sum(f.path.stat().st_size for f in frames) / 1024, 1),
        )
        return frames


def load_index(out_dir: Path) -> list[Frame] | None:
    """Load a previous :func:`extract_keyframes` result, or None if missing/stale."""
    index = out_dir / INDEX_NAME
    if not index.is_file():
        return None
    try:
        items = json.loads(index.read_text(encoding="utf-8"))
        frames = [
            Frame(int(d["index"]), float(d["time_s"]), out_dir / d["path"], str(d["phash"]))
            for d in items
        ]
    except (ValueError, KeyError, TypeError):
        return None
    root = out_dir.resolve()
    if not all(f.path.resolve().parent == root for f in frames):
        return None  # a tampered index must not point outside the frames folder
    return frames if all(f.path.is_file() for f in frames) else None


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1 has no sized default font
        return ImageFont.load_default()


def contact_sheet(
    frames: Sequence[Frame],
    out_path: Path,
    *,
    columns: int | None = None,
    thumb_height: int = 360,
    max_width: int = 1600,
    gap: int = 4,
    quality: int = 80,
) -> Path | None:
    """Tile ``frames`` into one JPEG grid with ``#index mm:ss`` burned into each tile.

    Args:
        frames: Frames to tile, in order.
        out_path: Destination JPEG.
        columns: Grid columns; by default chosen so the sheet is roughly square.
        thumb_height: Tile height before any width-fitting downscale.
        max_width: The whole sheet is shrunk to fit this width.
        gap: Pixels between tiles.
        quality: JPEG quality.

    Returns:
        ``out_path``, or None when ``frames`` is empty.
    """
    if not frames:
        return None
    with span("media.contact_sheet", frames=len(frames)) as s:
        with Image.open(frames[0].path) as first:
            aspect = first.width / first.height
            tile_h = min(thumb_height, first.height)
        tile_w = max(1, round(tile_h * aspect))
        cols = columns or max(1, math.ceil(math.sqrt(len(frames) / aspect)))
        cols = min(cols, len(frames))
        if cols * (tile_w + gap) - gap > max_width:
            tile_w = max(1, (max_width - (cols - 1) * gap) // cols)
            tile_h = max(1, round(tile_w / aspect))
        rows = math.ceil(len(frames) / cols)
        sheet = Image.new(
            "RGB", (cols * (tile_w + gap) - gap, rows * (tile_h + gap) - gap), (16, 16, 16)
        )
        draw = ImageDraw.Draw(sheet)
        font = _font(max(12, tile_h // 16))
        for n, frame in enumerate(frames):
            x, y = (n % cols) * (tile_w + gap), (n // cols) * (tile_h + gap)
            with Image.open(frame.path) as img:
                sheet.paste(img.convert("RGB").resize((tile_w, tile_h), Image.Resampling.LANCZOS),
                            (x, y))
            label = f"#{frame.index} {frame.timestamp}"
            left, top, right, bottom = draw.textbbox((0, 0), label, font=font)
            pad = 3
            draw.rectangle(
                (x, y, x + right - left + 2 * pad, y + bottom - top + 2 * pad), fill=(0, 0, 0)
            )
            draw.text((x + pad - left, y + pad - top), label, fill=(255, 230, 0), font=font)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(out_path, "JPEG", quality=quality, optimize=True)
        s.set(width=sheet.width, height=sheet.height, kb=round(out_path.stat().st_size / 1024, 1))
        attach_artifact(out_path, "contact_sheet")
        return out_path
