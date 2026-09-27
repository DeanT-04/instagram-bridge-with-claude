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
