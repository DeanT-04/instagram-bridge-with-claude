"""OCR: engine selection, ocr.json round-trip, hybrid re-spacing, and (when an engine is
installed) real recognition of drawn chart text."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from heliograph.media import ocr as ocr_mod
from heliograph.media.ocr import (
    HybridEngine,
    OcrLine,
    get_engine,
    load_ocr,
    ocr_frames,
)

TEXT = "ENTRY WHEN CANDLE WICKS THRU VWAP"


@dataclass
class F:
    index: int
    time_s: float
    path: Path


class FakeEngine:
    name = "fake"

    def read(self, image: Path) -> list[OcrLine]:
        if "bad" in image.name:
            raise RuntimeError("boom")
        return [OcrLine("EMA 9", 0.99, (1, 2, 30, 12)), OcrLine("5m", 0.9, None)]


def text_image(path: Path, text: str = TEXT, size: tuple[int, int] = (720, 400)) -> Path:
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    try:
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont = ImageFont.load_default(size=30)
    except TypeError:  # pragma: no cover - old Pillow
        font = ImageFont.load_default()
    d.text((20, 60), text, fill="black", font=font)
    d.text((20, 200), "EMA 9  |  5m  |  XAUUSD", fill=(30, 30, 200), font=font)
    img.save(path)
    return path


def test_engine_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    assert get_engine("off") is None
    with pytest.raises(ValueError, match="unknown OCR engine"):
        get_engine("tesseract")
    monkeypatch.setattr(ocr_mod, "_load", lambda name: None)
    assert get_engine("auto") is None and get_engine("rapidocr") is None
    monkeypatch.setenv("HELIOGRAPH_OCR_ENGINE", "off")
    from heliograph.config import get_settings

    get_settings.cache_clear()
    assert get_engine() is None


def test_ocr_frames_roundtrip(tmp_path: Path) -> None:
    good = text_image(tmp_path / "frame_001.jpg")
    bad = text_image(tmp_path / "bad_002.jpg")
    res = ocr_frames([F(1, 0.0, good), F(2, 2.5, bad)], tmp_path, FakeEngine())
    assert res.engine == "fake" and len(res.frames) == 2
    assert res.frames[0].text == "EMA 9 | 5m" and res.frames[1].lines == []
    loaded = load_ocr(tmp_path)
    assert loaded is not None and loaded.by_index()[1].lines[0].box == (1, 2, 30, 12)
    assert loaded.text == "EMA 9 | 5m"
    (tmp_path / "ocr.json").write_text("{not json", "utf-8")
    assert load_ocr(tmp_path) is None and load_ocr(tmp_path / "nope") is None


def test_hybrid_restores_spaces(tmp_path: Path) -> None:
    class Rapid:
        name = "rapidocr"

        def read(self, image: Path) -> list[OcrLine]:
            return [OcrLine("CANDLEWICKSORTAPSVWAP=CALLS", 0.99, (10, 10, 300, 30)),
                    OcrLine("12:25PM", 0.9, (0, 100, 50, 110))]

    class Win:
        name = "windows"

        def read(self, image: Path) -> list[OcrLine]:
            return [OcrLine("CANDLE WICKS OR TAPS VWAP = CALLS", None, (12, 11, 298, 29)),
                    OcrLine("12.•25PM", None, (0, 100, 50, 110))]

    eng = HybridEngine(Rapid(), Win())  # type: ignore[arg-type]
    lines = eng.read(tmp_path / "x.jpg")
    assert [ln.text for ln in lines] == ["CANDLE WICKS OR TAPS VWAP = CALLS", "12:25PM"]
    assert lines[0].score == 0.99


def test_real_engine_reads_drawn_text(tmp_path: Path) -> None:
    engine = get_engine("auto")
    if engine is None:
        pytest.skip("no OCR engine installed (uv sync --extra ocr)")
    lines = engine.read(text_image(tmp_path / "chart.png"))
    joined = " ".join(ln.text for ln in lines).upper().replace(" ", "")
    for word in ("CANDLE", "WICKS", "VWAP", "EMA9", "XAUUSD"):
        assert word in joined, (word, [ln.text for ln in lines])
