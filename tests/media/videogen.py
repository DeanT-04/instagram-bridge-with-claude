"""Generate tiny synthetic test videos with ffmpeg's lavfi sources (no fixtures on disk)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SPEECH = "hello world, this is a test of the transcription system"
_TWO_SCENES = (
    "testsrc=d=2:s=320x240:r=10[a];smptebars=d=2:s=320x240:r=10[b];[a][b]concat=n=2[out0]"
)


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe is None:
        pytest.skip("ffmpeg not on PATH")
    return exe


def _has_flite(exe: str) -> bool:
    out = subprocess.run([exe, "-hide_banner", "-filters"], capture_output=True, text=True,
                         check=False, timeout=30).stdout
    return " flite " in out


def make_video(path: Path, *, audio: bool = True) -> Path:
    """Write a ~4 s 320x240 H.264 video with a hard cut at 2 s.

    With ``audio=True`` the track is synthesised speech (flite) when available, else a sine.
    """
    exe = _ffmpeg()
    args = [exe, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", _TWO_SCENES]
    if audio:
        src = (f"flite=text='{SPEECH}':voice=slt" if _has_flite(exe)
               else "sine=frequency=440:duration=4")
        args += ["-f", "lavfi", "-i", src, "-af", "apad", "-c:a", "aac"]
    args += ["-t", "4", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(args, check=True, timeout=120, capture_output=True)
    return path


def make_text_video(path: Path, texts: list[str], *, seconds_each: float = 2.0) -> Path:
    """A silent 640x360 video showing each of ``texts`` (big black on white) in turn,
    built from PIL-drawn frames - for OCR tests."""
    from PIL import Image, ImageDraw, ImageFont

    exe = _ffmpeg()
    work = path.parent / f"{path.stem}_slides"
    work.mkdir(parents=True, exist_ok=True)
    try:
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont = ImageFont.load_default(size=26)
    except TypeError:  # pragma: no cover - Pillow < 10.1
        font = ImageFont.load_default()
    for i, text in enumerate(texts):
        img = Image.new("RGB", (640, 360), "white")
        draw = ImageDraw.Draw(img)
        draw.rectangle((0, 250, 640, 360), fill=(20, 20, 40))  # a "chart" band
        draw.text((12, 120), text, fill="black", font=font)
        img.save(work / f"s{i:03d}.png")
    subprocess.run(
        [exe, "-hide_banner", "-loglevel", "error", "-y", "-framerate", f"1/{seconds_each}",
         "-i", str(work / "s%03d.png"), "-vf", "fps=10,format=yuv420p", "-c:v", "libx264",
         "-preset", "ultrafast", str(path)],
        check=True, timeout=120, capture_output=True)
    return path
