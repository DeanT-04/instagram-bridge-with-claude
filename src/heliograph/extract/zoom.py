"""Crop and zoom a region of a dossier keyframe so small chart text becomes legible.

``box`` is either a preset name (``top``, ``bottom-left``, ``middle-third``...), four
fractions ``x0,y0,x1,y1`` of the frame (all values <= 1) or four pixel coordinates. The crop
is upscaled (LANCZOS) so its longer side reaches ``target`` px (at most ``max_scale``x) and
written as PNG to ``<dossier>/crops/``; optionally it is OCR'd too (upscaling helps OCR on
tiny labels).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from heliograph.eye import span
from heliograph.media.frames import load_index
from heliograph.media.ocr import get_engine

__all__ = ["PRESETS", "Crop", "crop_frame", "frame_path", "parse_box"]

_T = 1 / 3
PRESETS: dict[str, tuple[float, float, float, float]] = {
    "full": (0, 0, 1, 1),
    "top": (0, 0, 1, 0.5), "bottom": (0, 0.5, 1, 1),
    "left": (0, 0, 0.5, 1), "right": (0.5, 0, 1, 1),
    "center": (0.25, 0.25, 0.75, 0.75),
    "top-left": (0, 0, 0.5, 0.5), "top-right": (0.5, 0, 1, 0.5),
    "bottom-left": (0, 0.5, 0.5, 1), "bottom-right": (0.5, 0.5, 1, 1),
    "top-third": (0, 0, 1, _T), "middle-third": (0, _T, 1, 2 * _T),
    "bottom-third": (0, 2 * _T, 1, 1),
}
BoxLike = str | Sequence[float]


@dataclass(frozen=True)
class Crop:
    """A written crop."""

    path: Path
    frame_index: int
    source: Path
    box: tuple[int, int, int, int]
    scale: float
    size: tuple[int, int]
    ocr_text: str | None = None


def parse_box(box: BoxLike, width: int, height: int) -> tuple[int, int, int, int]:
    """Pixel box ``(x0, y0, x1, y1)`` for a preset / fraction / pixel ``box``."""
    if isinstance(box, str):
        key = box.strip().lower().replace("_", "-").replace(" ", "-")
        if key in PRESETS:
            values: Sequence[float] = PRESETS[key]
        else:
            parts = [p for p in re.split(r"[,\s]+", box.strip()) if p]
            try:
                values = [float(p) for p in parts]
            except ValueError:
                raise ValueError(f"Unknown crop {box!r}; use x0,y0,x1,y1 or one of: "
                                 f"{', '.join(PRESETS)}") from None
    else:
        values = [float(v) for v in box]
    if len(values) != 4:
        raise ValueError(f"A crop box needs 4 numbers (x0,y0,x1,y1), got {len(values)}")
    x0, y0, x1, y1 = values
    if all(0 <= v <= 1 for v in values):
        x0, x1, y0, y1 = x0 * width, x1 * width, y0 * height, y1 * height
    px = (max(0, round(x0)), max(0, round(y0)), min(width, round(x1)), min(height, round(y1)))
    if px[2] - px[0] < 4 or px[3] - px[1] < 4:
        raise ValueError(f"Crop box {tuple(values)} is empty or outside the "
                         f"{width}x{height} frame")
    return px


def frame_path(folder: Path, frame_index: int) -> Path:
    """Keyframe ``frame_index`` of a dossier (or the n-th image of a photo/carousel post)."""
    frames = load_index(folder / "frames") or []
    by_index = {f.index: f.path for f in frames}
    if not by_index and (folder / "images").is_dir():
        images = sorted(p for p in (folder / "images").iterdir() if p.is_file())
        by_index = {i: p for i, p in enumerate(images, start=1)}
    if frame_index not in by_index:
        raise ValueError(f"No frame #{frame_index}; available: {sorted(by_index)[:50]}")
    return by_index[frame_index]


def crop_frame(folder: Path, frame_index: int, box: BoxLike, *, scale: float | None = None,
               target: int = 1600, max_scale: float = 4.0, ocr: bool = False,
               ocr_engine: str | None = None) -> Crop:
    """Crop ``box`` out of keyframe ``frame_index`` of the dossier in ``folder``, upscale it
    and save it to ``folder/crops/``.

    Args:
        folder: Dossier folder.
        frame_index: 1-based keyframe index from ``frames.json`` / dossier.md.
        box: Preset name, fractions or pixels (see module docs).
        scale: Explicit zoom factor (default: reach ``target`` px, capped at ``max_scale``).
        target: Longer side of the output when ``scale`` is None.
        max_scale: Upper bound for the automatic zoom factor.
        ocr: Also OCR the zoomed crop (if an OCR engine is installed).
        ocr_engine: Override ``Settings.ocr_engine``.
    """
    src = frame_path(folder, frame_index)
    with span("extract.crop", frame=frame_index) as s, Image.open(src) as img:
        px = parse_box(box, img.width, img.height)
        w, h = px[2] - px[0], px[3] - px[1]
        factor = scale if scale is not None else min(max_scale, max(1.0, target / max(w, h)))
        factor = max(0.25, min(float(factor), 8.0))
        size = (max(1, round(w * factor)), max(1, round(h * factor)))
        region = img.convert("RGB").crop(px).resize(size, Image.Resampling.LANCZOS)
        out_dir = folder / "crops"
        out_dir.mkdir(exist_ok=True)
        out = out_dir / (f"frame_{frame_index:03d}_{px[0]}-{px[1]}-{px[2]}-{px[3]}"
                         f"_x{factor:.2g}.png")
        region.save(out, "PNG", optimize=True)
        s.set(box=px, scale=round(factor, 2), size=size)
    text = None
    if ocr:
        engine = get_engine(ocr_engine)
        text = " | ".join(ln.text for ln in engine.read(out)) if engine else None
    return Crop(out, frame_index, src, px, round(factor, 2), size, text)
