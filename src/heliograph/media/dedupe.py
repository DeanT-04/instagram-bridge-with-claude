"""Near-duplicate keyframe removal, tuned for trading reels.

Three rules, applied in time order to the candidate frames (the first is always kept):

1. **Previous-frame rule** - perceptual hash (pHash) within ``max_distance`` of the last kept
   frame (the original behaviour: slow drift is still sampled).
2. **Seen-before rule** - pHash within ``global_distance`` of *any* kept frame, so a video
   that cuts A -> B -> A does not keep A twice.
3. **Static-region rule** - a talking-head overlay or burned-in word-by-word subtitle keeps
   changing while the chart/end-card behind it is frozen (e.g. a 30 s CTA tail). Tiles of a
   coarse grid that change in at least ``live_rate`` of consecutive candidate pairs are
   marked *live* (webcam, subtitles); a frame whose non-live tiles are all unchanged versus
   the last kept frame is a duplicate. Live masking is disabled when most of the frame is
   live (hand-held phone footage), and needs at least ``MIN_PAIRS`` candidate pairs.

Thresholds were measured on six real trading reels: one CTA reel goes from
15 tail frames to 4 while annotation changes on small dark charts (thin circles, R:R boxes)
survive.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import imagehash
import numpy as np
from numpy.typing import NDArray
from PIL import Image

__all__ = ["DedupeStats", "dedupe"]

TILE = 32
MIN_PAIRS = 5


@dataclass
class DedupeStats:
    """What :func:`dedupe` dropped and why."""

    previous: int = 0
    seen_before: int = 0
    static: int = 0
    live_tiles: int = 0


def _grid(width: int, height: int) -> tuple[int, int]:
    return (12, 6) if height >= width else (6, 12)


def _changed(a: NDArray[np.int16], b: NDArray[np.int16], rows: int, cols: int,
             pixel_delta: int, min_pixels: int) -> NDArray[np.bool_]:
    strong = np.abs(a - b) > pixel_delta
    counts = strong.reshape(rows, TILE, cols, TILE).sum(axis=(1, 3))
    return np.asarray(counts >= min_pixels)


def dedupe(
    candidates: Sequence[tuple[float, Path]],
    *,
    hash_size: int = 16,
    max_distance: int = 22,
    global_distance: int = 10,
    live_rate: float = 0.5,
    max_live_fraction: float = 0.4,
    pixel_delta: int = 35,
    min_pixels: int = 3,
    stats: DedupeStats | None = None,
) -> list[tuple[float, Path, imagehash.ImageHash]]:
    """Return the candidates worth keeping as ``(time, path, phash)``.

    ``max_distance < 0`` disables deduplication entirely (every candidate is kept).
    """
    st = stats if stats is not None else DedupeStats()
    hashes: list[imagehash.ImageHash] = []
    grays: list[NDArray[np.int16]] = []
    rows = cols = 0
    for _, path in candidates:
        with Image.open(path) as img:
            hashes.append(imagehash.phash(img, hash_size=hash_size))
            if not rows:
                rows, cols = _grid(img.width, img.height)
            small = img.convert("L").resize((cols * TILE, rows * TILE), Image.Resampling.BILINEAR)
            grays.append(np.asarray(small, dtype=np.int16))
    if max_distance < 0:
        return [(t, p, h) for (t, p), h in zip(candidates, hashes, strict=True)]

    live = np.zeros((rows, cols), dtype=bool)
    if len(grays) > MIN_PAIRS:
        rate = np.mean([_changed(grays[i], grays[i - 1], rows, cols, pixel_delta, min_pixels)
                        for i in range(1, len(grays))], axis=0)
        live = rate >= live_rate
        # Hysteresis: the fringe of a webcam/subtitle area changes less often; neighbours
        # of live tiles that still change in at least half as many pairs are live too.
        near = np.zeros_like(live)
        near[1:, :] |= live[:-1, :]
        near[:-1, :] |= live[1:, :]
        near[:, 1:] |= live[:, :-1]
        near[:, :-1] |= live[:, 1:]
        live |= near & (rate >= live_rate / 2)
        if live.mean() > max_live_fraction:
            live[:] = False
    st.live_tiles = int(live.sum())

    kept: list[int] = []
    for i in range(len(candidates)):
        if kept:
            last = kept[-1]
            if hashes[i] - hashes[last] <= max_distance:
                st.previous += 1
                continue
            if global_distance >= 0 and min(hashes[i] - hashes[k] for k in kept) \
                    <= global_distance:
                st.seen_before += 1
                continue
            if not (_changed(grays[i], grays[last], rows, cols, pixel_delta,
                                            min_pixels) & ~live).any():
                st.static += 1
                continue
        kept.append(i)
    return [(candidates[i][0], candidates[i][1], hashes[i]) for i in kept]
