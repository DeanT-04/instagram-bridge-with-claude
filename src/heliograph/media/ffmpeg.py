"""Thin, safe wrappers around the ``ffmpeg`` / ``ffprobe`` executables.

Commands are always run as argument lists (never through a shell), with a timeout, and
each invocation is recorded as an eye span. Stderr is captured so callers can parse
filter output (e.g. ``showinfo``).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from heliograph.errors import HeliographError
from heliograph.eye import span

__all__ = ["FFmpegError", "MediaInfo", "input_args", "output_path", "probe", "require", "run"]


class FFmpegError(HeliographError):
    """ffmpeg/ffprobe is missing, timed out or exited with an error."""

    default_hint = "Install ffmpeg (and ffprobe) and make sure both are on PATH."


def require(tool: str) -> str:
    """Return the absolute path of ``tool`` (``ffmpeg``/``ffprobe``) or raise FFmpegError."""
    path = shutil.which(tool)
    if path is None:
        raise FFmpegError(f"{tool} not found on PATH")
    return path


def input_args(path: Path) -> list[str]:
    """``-i`` arguments for an untrusted local media file.

    * ``-protocol_whitelist file``: a downloaded "video" that is really an HLS/concat
      playlist cannot make ffmpeg fetch URLs (SSRF) or pull in other protocols.
    * The path is made absolute, so a name starting with ``-`` or containing ``:`` can never
      be read as an option or a protocol prefix.
    """
    return ["-protocol_whitelist", "file", "-i", str(Path(path).resolve())]


def output_path(path: Path) -> str:
    """Absolute output path (never mistaken for an option or protocol)."""
    return str(Path(path).resolve())


def run(tool: str, args: list[str], *, timeout: float, op: str) -> subprocess.CompletedProcess[str]:
    """Run ``tool args...`` and return the completed process (text mode, output captured).

    Args:
        tool: ``"ffmpeg"`` or ``"ffprobe"``.
        args: Arguments after the executable name.
        timeout: Seconds before the process is killed.
        op: Short operation label recorded on the span (``frames``, ``audio``...).

    Raises:
        FFmpegError: on a missing executable, timeout or non-zero exit status.
    """
    exe = require(tool)
    with span(f"media.{tool}", op=op, timeout_s=timeout) as s:
        t0 = time.perf_counter()
        try:
            proc = subprocess.run(
                [exe, *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired as exc:
            raise FFmpegError(f"{tool} ({op}) timed out after {timeout:.0f}s") from exc
        s.set(returncode=proc.returncode, elapsed_s=round(time.perf_counter() - t0, 3))
        if proc.returncode != 0:
            tail = (proc.stderr or "").strip().splitlines()[-5:]
            raise FFmpegError(
                f"{tool} ({op}) failed with exit {proc.returncode}: {' | '.join(tail)}"
            )
        return proc


@dataclass(frozen=True)
class MediaInfo:
    """Basic facts about a media file from ``ffprobe``."""

    duration: float | None
    width: int | None
    height: int | None
    has_audio: bool
    has_video: bool


def probe(path: Path, *, timeout: float = 60) -> MediaInfo:
    """Inspect ``path`` with ffprobe (duration, first video stream size, audio presence)."""
    proc = run(
        "ffprobe",
        ["-v", "error", "-print_format", "json", "-show_format", "-show_streams",
         "-protocol_whitelist", "file", str(Path(path).resolve())],
        timeout=timeout,
        op="probe",
    )
    data: dict[str, Any] = json.loads(proc.stdout or "{}")
    streams: list[dict[str, Any]] = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = any(s.get("codec_type") == "audio" for s in streams)
    raw_duration = (data.get("format") or {}).get("duration")
    try:
        duration = float(raw_duration) if raw_duration is not None else None
    except ValueError:
        duration = None
    return MediaInfo(
        duration=duration,
        width=int(video["width"]) if video and video.get("width") else None,
        height=int(video["height"]) if video and video.get("height") else None,
        has_audio=audio,
        has_video=video is not None,
    )
