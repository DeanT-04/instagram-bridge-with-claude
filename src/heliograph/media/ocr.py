"""On-screen text (OCR) for keyframes: chart labels, overlays, indicator settings.

Engines, best first (``engine="auto"`` picks the first one installed):

* ``rapidocr`` - `rapidocr-onnxruntime` (PP-OCR models bundled in the wheel, CPU via
  onnxruntime, no network). Most accurate on small UI/chart text; ~2 s per 720x1280 frame.
  Install with ``uv sync --extra ocr``.
* ``rapidocr+windows`` - what ``auto`` picks when both are installed: rapidocr's text with
  word spacing restored from Windows OCR (rapidocr tends to drop spaces).
* ``windows`` - the OCR engine built into Windows 10/11 (``Windows.Media.Ocr`` via the
  ``winrt-*`` projection packages; uses the installed OCR language packs). ~0.1 s per frame
  and good on large overlay text, weak on small dark-theme UI text. ``uv sync --extra
  ocr-windows``.

Neither is a hard dependency: without them :func:`get_engine` returns None and the dossier
simply has no ``frames/ocr.json``. Tesseract is deliberately not used (needs a system
binary).
"""

from __future__ import annotations

import asyncio
import difflib
import json
import re
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

from heliograph.eye import span

__all__ = [
    "OCR_NAME",
    "FrameText",
    "OcrEngine",
    "OcrLine",
    "OcrResult",
    "available_engines",
    "get_engine",
    "load_ocr",
    "ocr_frames",
]

OCR_NAME = "ocr.json"
ENGINES = ("rapidocr", "windows")
_JUNK = re.compile(r"^[\W_]{0,3}$|^.$")


@dataclass(frozen=True)
class OcrLine:
    """One recognised line of text; ``box`` is ``(x0, y0, x1, y1)`` in image pixels."""

    text: str
    score: float | None = None
    box: tuple[int, int, int, int] | None = None


@dataclass
class FrameText:
    """OCR of one keyframe."""

    index: int
    time_s: float
    path: str
    lines: list[OcrLine] = field(default_factory=list)

    @property
    def text(self) -> str:
        """Lines joined with `` | `` (reading order)."""
        return " | ".join(ln.text for ln in self.lines)


@dataclass
class OcrResult:
    """``frames/ocr.json``."""

    engine: str
    elapsed_s: float
    frames: list[FrameText] = field(default_factory=list)

    def by_index(self) -> dict[int, FrameText]:
        """Frame index -> its OCR."""
        return {f.index: f for f in self.frames}

    @property
    def text(self) -> str:
        """All recognised text (for keyword/regex scans)."""
        return "\n".join(f.text for f in self.frames if f.lines)

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly representation."""
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> OcrResult:
        """Inverse of :meth:`to_dict`."""
        frames = [
            FrameText(int(f["index"]), float(f["time_s"]), str(f["path"]),
                      [OcrLine(str(ln["text"]), ln.get("score"),
                               tuple(ln["box"]) if ln.get("box") else None)
                       for ln in f.get("lines", [])])
            for f in d.get("frames", [])
        ]
        return cls(str(d.get("engine", "")), float(d.get("elapsed_s", 0.0)), frames)


class OcrEngine(Protocol):
    """Anything that turns an image file into text lines."""

    name: str

    def read(self, image: Path) -> list[OcrLine]:
        """Recognise the text in ``image``."""
        ...


def _bbox(points: Sequence[Sequence[float]]) -> tuple[int, int, int, int]:
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return round(min(xs)), round(min(ys)), round(max(xs)), round(max(ys))


class RapidEngine:
    """rapidocr-onnxruntime (bundled PP-OCR det + rec models)."""

    name = "rapidocr"

    def __init__(self, min_score: float = 0.6) -> None:
        from rapidocr_onnxruntime import RapidOCR

        self._ocr = RapidOCR()
        self.min_score = min_score

    def read(self, image: Path) -> list[OcrLine]:
        """Recognise text (angle classifier off: frames are upright)."""
        # bytes, not a path: OpenCV cannot open non-ASCII Windows paths
        result, _ = self._ocr(Path(image).read_bytes(), use_cls=False)
        lines = [OcrLine(" ".join(str(text).split()), round(float(score), 3), _bbox(box))
                 for box, text, score in (result or []) if float(score) >= self.min_score]
        return [ln for ln in lines if not _JUNK.match(ln.text)]


class WindowsEngine:
    """Windows.Media.Ocr (built into Windows 10/11) through the winrt projection."""

    name = "windows"

    def __init__(self) -> None:
        from winrt.windows.media.ocr import OcrEngine as WinOcr

        engine = WinOcr.try_create_from_user_profile_languages()
        if engine is None:
            raise RuntimeError("no Windows OCR language pack is installed")
        self._engine = engine

    async def _recognize(self, image: Path) -> list[OcrLine]:
        from PIL import Image
        from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.security.cryptography import CryptographicBuffer

        with Image.open(image) as img:
            r, g, b, a = img.convert("RGBA").split()
            bgra = Image.merge("RGBA", (b, g, r, a))
            width, height, data = bgra.width, bgra.height, bgra.tobytes()
        buf = CryptographicBuffer.create_from_byte_array(data)
        bitmap = SoftwareBitmap.create_copy_from_buffer(buf, BitmapPixelFormat.BGRA8,
                                                        width, height)
        res = await self._engine.recognize_async(bitmap)
        out: list[OcrLine] = []
        for line in res.lines:
            rects = [w.bounding_rect for w in line.words]
            box = (round(min(r.x for r in rects)), round(min(r.y for r in rects)),
                   round(max(r.x + r.width for r in rects)),
                   round(max(r.y + r.height for r in rects))) if rects else None
            out.append(OcrLine(" ".join(str(line.text).split()), None, box))
        return [ln for ln in out if not _JUNK.match(ln.text)]

    def read(self, image: Path) -> list[OcrLine]:
        """Recognise text (runs the WinRT async call on a private event loop)."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self._recognize(image))
        with ThreadPoolExecutor(1) as pool:  # called from async code: use a fresh thread
            return pool.submit(asyncio.run, self._recognize(image)).result()


def _overlap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1])) or 1
    return ix * iy / smaller


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


class HybridEngine:
    """rapidocr for recognition, Windows OCR only to restore word spacing.

    rapidocr's bundled recogniser often drops spaces ("CANDLEWICKSORTAPS..."); when a
    Windows OCR line covers the same box and has the same characters, its spacing is used.
    """

    name = "rapidocr+windows"

    def __init__(self, rapid: RapidEngine, windows: WindowsEngine) -> None:
        self.rapid, self.windows = rapid, windows

    def read(self, image: Path) -> list[OcrLine]:
        """Recognise with rapidocr, then re-space lines using Windows OCR."""
        lines = self.rapid.read(image)
        if not any(" " not in ln.text and len(ln.text) > 8 for ln in lines):
            return lines
        try:
            win = [w for w in self.windows.read(image) if w.box]
        except Exception:
            return lines
        out = []
        for ln in lines:
            match = next((w for w in win if ln.box and w.box and _overlap(ln.box, w.box) > 0.5
                          and difflib.SequenceMatcher(None, _squash(w.text),
                                                      _squash(ln.text)).ratio() >= 0.9), None)
            out.append(OcrLine(match.text, ln.score, ln.box)
                       if match and match.text.count(" ") > ln.text.count(" ") else ln)
        return out


_FACTORIES: dict[str, type[RapidEngine] | type[WindowsEngine]] = {
    "rapidocr": RapidEngine, "windows": WindowsEngine,
}


@lru_cache(maxsize=4)
def _load(name: str) -> OcrEngine | None:
    try:
        return _FACTORIES[name]()
    except Exception:  # not installed / no language pack / broken runtime
        return None


def available_engines() -> list[str]:
    """Engines that can actually be loaded on this machine."""
    return [n for n in ENGINES if _load(n) is not None]


def get_engine(name: str | None = None) -> OcrEngine | None:
    """The requested engine (``auto`` = first available; ``off`` = None)."""
    if name is None:
        from heliograph.config import get_settings

        name = get_settings().ocr_engine
    if name == "off":
        return None
    if name == "auto":
        rapid, windows = _load("rapidocr"), _load("windows")
        if isinstance(rapid, RapidEngine) and isinstance(windows, WindowsEngine):
            return HybridEngine(rapid, windows)
        return rapid or windows
    if name not in _FACTORIES:
        raise ValueError(f"unknown OCR engine {name!r}; use auto, {', '.join(ENGINES)} or off")
    return _load(name)


def ocr_frames(frames: Sequence[Any], out_dir: Path | None, engine: OcrEngine) -> OcrResult:
    """OCR every frame (objects with ``index``, ``time_s``, ``path``); writes ``ocr.json``
    into ``out_dir`` when given. A frame that fails is recorded with no lines."""
    t0 = time.perf_counter()
    with span("media.ocr", engine=engine.name, frames=len(frames)) as s:
        result = OcrResult(engine.name, 0.0)
        failed = 0
        for f in frames:
            path = Path(f.path)
            try:
                lines = engine.read(path)
            except Exception:
                failed += 1
                lines = []
            result.frames.append(FrameText(int(f.index), float(f.time_s), path.name, lines))
        result.elapsed_s = round(time.perf_counter() - t0, 2)
        s.set(failed=failed, lines=sum(len(f.lines) for f in result.frames),
              chars=len(result.text), elapsed_s=result.elapsed_s)
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / OCR_NAME).write_text(
            json.dumps(result.to_dict(), indent=2, ensure_ascii=False), "utf-8")
    return result


def load_ocr(out_dir: Path) -> OcrResult | None:
    """Load ``ocr.json`` from ``out_dir`` if present and valid."""
    p = out_dir / OCR_NAME
    if not p.is_file():
        return None
    try:
        return OcrResult.from_dict(json.loads(p.read_text("utf-8")))
    except (ValueError, KeyError, TypeError):
        return None
