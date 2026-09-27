"""Keyframe dedupe rules on PIL-made frames (static chart + moving webcam, A-B-A cuts)."""

from __future__ import annotations

import random
from pathlib import Path

from PIL import Image, ImageDraw

from heliograph.media.dedupe import DedupeStats, dedupe


def _chart(draw: ImageDraw.ImageDraw, seed: int) -> None:
    rnd = random.Random(seed)
    y = 300
    for x in range(20, 340, 12):
        y = max(60, min(560, y + rnd.randint(-40, 40)))
        draw.rectangle((x, y, x + 6, y + rnd.randint(10, 40)), fill=(20, 160, 60))


def _frame(path: Path, *, chart_seed: int, face_seed: int, circle: bool = False,
           word: str = "") -> Path:
    """360x640 vertical frame: chart on top (y<640*0.6), noisy 'webcam' at the bottom."""
    img = Image.new("RGB", (360, 640), "white")
    d = ImageDraw.Draw(img)
    _chart(d, chart_seed)
    rnd = random.Random(face_seed)
    for _ in range(400):  # a moving face: blobs at random places in the bottom band
        x, y = rnd.randint(110, 250), rnd.randint(480, 610)
        r = rnd.randint(4, 14)
        d.ellipse((x - r, y - r, x + r, y + r), fill=(rnd.randint(90, 230), 120, 90))
    if circle:
        d.ellipse((120, 150, 260, 260), outline=(220, 30, 30), width=5)
    if word:
        d.text((150, 20), word, fill="black")
    img.save(path, "JPEG", quality=92)
    return path


def test_static_tail_collapses_but_annotations_survive(tmp_path: Path) -> None:
    cands: list[tuple[float, Path]] = []
    # 4 different charts, then a 12-frame static tail where only the webcam moves, with
    # one annotation (a red circle) drawn on the chart halfway through the tail.
    for i in range(4):
        cands.append((i * 2.0, _frame(tmp_path / f"a{i}.jpg", chart_seed=i, face_seed=i)))
    for j in range(12):
        t = 8.0 + j * 2.0
        cands.append((t, _frame(tmp_path / f"t{j}.jpg", chart_seed=99, face_seed=100 + j,
                                circle=j >= 6, word=f"w{j}")))
    stats = DedupeStats()
    kept = dedupe(cands, stats=stats)
    times = [t for t, _, _ in kept]
    tail = [t for t in times if t >= 8.0]
    assert times[:4] == [0.0, 2.0, 4.0, 6.0]
    # Previous-frame pHash alone keeps every tail frame (the webcam changes too much).
    assert len(dedupe(cands, global_distance=-1, live_rate=2.0)) >= len(cands) - 1
    # The static-region rule removes at least half of the frozen-chart tail...
    assert len(tail) <= 6, times
    assert stats.static >= 6 and stats.live_tiles > 0
    # ...but the frame where the circle is drawn on the chart (from t=20) survives.
    assert 20.0 in tail

    everything = dedupe(cands, max_distance=-1)
    assert len(everything) == len(cands)


def test_seen_before_rule(tmp_path: Path) -> None:
    a = _frame(tmp_path / "a.jpg", chart_seed=1, face_seed=1)
    b = _frame(tmp_path / "b.jpg", chart_seed=2, face_seed=2)
    a2 = tmp_path / "a2.jpg"
    a2.write_bytes(a.read_bytes())
    stats = DedupeStats()
    kept = dedupe([(0.0, a), (2.0, b), (4.0, a2)], stats=stats)
    assert [t for t, _, _ in kept] == [0.0, 2.0]
    assert stats.seen_before == 1
