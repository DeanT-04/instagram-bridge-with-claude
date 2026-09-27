"""Keyframe extraction and contact sheet tests on a synthetic video."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from heliograph.media.frames import (
    INDEX_NAME,
    Frame,
    contact_sheet,
    extract_keyframes,
    format_ts,
    load_index,
    pick_evenly,
)


def test_pick_evenly() -> None:
    assert pick_evenly(5, 10) == [0, 1, 2, 3, 4]
    assert pick_evenly(100, 1) == [0]
    assert pick_evenly(0, 4) == []
    picked = pick_evenly(50, 24)
    assert len(picked) == 24 and picked[0] == 0 and picked[-1] == 49


def test_format_ts() -> None:
    assert format_ts(0) == "00:00"
    assert format_ts(75.9) == "01:15"
    assert format_ts(3725) == "1:02:05"


def test_extract_keyframes(video: Path, tmp_path: Path) -> None:
    out = tmp_path / "frames"
    frames = extract_keyframes(video, out, max_side=120)
    assert len(frames) >= 2  # the first frame and the hard cut at 2 s
    times = [f.time_s for f in frames]
    assert times == sorted(times) and times[0] == 0.0
    assert any(abs(t - 2.0) < 0.3 for t in times)
    assert [f.index for f in frames] == list(range(1, len(frames) + 1))
    for f in frames:
        with Image.open(f.path) as img:
            assert img.format == "JPEG" and img.size == (120, 90)
    assert not (out / ".raw").exists()
    index = json.loads((out / INDEX_NAME).read_text("utf-8"))
    assert [d["time_s"] for d in index] == times
    assert load_index(out) == frames


def test_never_upscales(video: Path, tmp_path: Path) -> None:
    frames = extract_keyframes(video, tmp_path)
    with Image.open(frames[0].path) as img:
        assert img.size == (320, 240)


def test_dedupe_and_cap(video: Path, tmp_path: Path) -> None:
    # Sample every 0.2 s: many near-identical frames within each scene.
    no_dedupe = extract_keyframes(video, tmp_path / "a", min_interval=0.2, max_distance=-1)
    deduped = extract_keyframes(video, tmp_path / "b", min_interval=0.2)
    assert len(no_dedupe) > len(deduped) >= 2
    capped = extract_keyframes(
        video, tmp_path / "c", min_interval=0.2, max_distance=-1, max_frames=3
    )
    assert len(capped) == 3
    assert capped[0].time_s == 0.0 and capped[-1].time_s == no_dedupe[-1].time_s
    assert len(list((tmp_path / "c").glob("*.jpg"))) == 3


def test_load_index_missing_or_stale(tmp_path: Path) -> None:
    assert load_index(tmp_path) is None
    (tmp_path / INDEX_NAME).write_text(
        json.dumps([{"index": 1, "time_s": 0, "path": "gone.jpg", "phash": "0"}]), "utf-8"
    )
    assert load_index(tmp_path) is None


def test_contact_sheet(video: Path, tmp_path: Path) -> None:
    frames = extract_keyframes(video, tmp_path / "f", min_interval=0.5, max_distance=-1)
    sheet = contact_sheet(frames, tmp_path / "sheet.jpg", thumb_height=100)
    assert sheet is not None
    with Image.open(sheet) as img:
        assert img.format == "JPEG"
        assert img.height >= 100 and img.width <= 1600
        # Top-left of the first tile is the black label box, not the video.
        r, g, b = img.convert("RGB").getpixel((1, 1))  # type: ignore[misc]
        assert max(r, g, b) < 40
    assert contact_sheet([], tmp_path / "none.jpg") is None


def test_contact_sheet_fits_max_width(tmp_path: Path) -> None:
    frames = []
    for i in range(6):
        p = tmp_path / f"{i}.jpg"
        Image.new("RGB", (400, 200), (i * 40, 0, 0)).save(p)
        frames.append(Frame(i + 1, float(i), p, "0"))
    sheet = contact_sheet(frames, tmp_path / "s.jpg", columns=6, thumb_height=200, max_width=600)
    assert sheet is not None
    with Image.open(sheet) as img:
        assert img.width <= 600
