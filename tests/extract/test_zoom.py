"""crop_frame / parse_box on a synthetic dossier folder."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from heliograph.extract.zoom import PRESETS, crop_frame, parse_box
from heliograph.media import ocr as ocr_mod
from heliograph.media.ocr import OcrLine


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    frames = tmp_path / "d" / "frames"
    frames.mkdir(parents=True)
    img = Image.new("RGB", (720, 1280), "white")
    img.paste((255, 0, 0), (360, 0, 720, 640))  # top-right quadrant is red
    img.save(frames / "frame_001.jpg", quality=95)
    (frames / "frames.json").write_text(json.dumps(
        [{"index": 1, "time_s": 0.0, "path": "frame_001.jpg", "phash": "0"}]), "utf-8")
    return tmp_path / "d"


def test_parse_box() -> None:
    assert parse_box("top-right", 720, 1280) == (360, 0, 720, 640)
    assert parse_box("Bottom Third", 720, 1280) == (0, 853, 720, 1280)
    assert parse_box("0.5,0,1,0.5", 720, 1280) == (360, 0, 720, 640)
    assert parse_box([10, 20, 110, 220], 720, 1280) == (10, 20, 110, 220)
    assert parse_box("0 0 5000 5000", 720, 1280) == (0, 0, 720, 1280)
    assert set(PRESETS) >= {"top", "bottom", "center", "middle-third"}
    for bad in ("nowhere", "1,2,3", "0.5,0.5,0.5,0.5", [900, 900, 1000, 1000]):
        with pytest.raises(ValueError):
            parse_box(bad, 720, 1280)


def test_crop_frame_zooms_and_saves(folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    c = crop_frame(folder, 1, "top-right")
    assert c.path.parent == folder / "crops" and c.path.suffix == ".png"
    assert c.box == (360, 0, 720, 640) and c.scale == 2.5 and c.size == (900, 1600)
    with Image.open(c.path) as img:
        assert img.size == (900, 1600)
        r, g, _ = img.getpixel((450, 800))  # type: ignore[misc]
        assert r > 240 and g < 20
    assert c.ocr_text is None
    small = crop_frame(folder, 1, "0.9,0.9,1,1", scale=2)
    assert small.scale == 2 and small.size == (144, 256)
    capped = crop_frame(folder, 1, [0, 0, 40, 40])
    assert capped.scale == 4.0  # auto zoom is capped at 4x

    class Fake:
        name = "fake"

        def read(self, image: Path) -> list[OcrLine]:
            return [OcrLine("EMA 9")]

    monkeypatch.setattr(ocr_mod, "_load", lambda name: Fake())
    assert crop_frame(folder, 1, "top", ocr=True, ocr_engine="rapidocr").ocr_text == "EMA 9"
    with pytest.raises(ValueError, match="No frame #7"):
        crop_frame(folder, 7, "top")


def test_crop_falls_back_to_post_images(tmp_path: Path) -> None:
    images = tmp_path / "p" / "images"
    images.mkdir(parents=True)
    Image.new("RGB", (100, 100), "blue").save(images / "01.jpg")
    c = crop_frame(tmp_path / "p", 1, "center", scale=1)
    assert c.size == (50, 50) and c.source.name == "01.jpg"
