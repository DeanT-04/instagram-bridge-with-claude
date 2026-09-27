"""Speech-to-text with faster-whisper (CPU, int8), producing timestamped segments.

Audio is first extracted with ffmpeg to 16 kHz mono WAV. Videos without an audio stream
yield an empty :class:`Transcript` with ``has_audio=False`` rather than an error. Models
are loaded lazily and cached per ``(size, device, compute_type)``; the default size comes
from ``Settings.whisper_model``.
"""

from __future__ import annotations

import json
import tempfile
import time
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from heliograph.config import get_settings
from heliograph.eye import span
from heliograph.media.ffmpeg import input_args, output_path, probe, run
from heliograph.media.frames import format_ts

__all__ = [
    "Segment",
    "Transcript",
    "extract_audio",
    "get_model",
    "load_transcript",
    "transcribe",
    "write_transcript",
]

JSON_NAME = "transcript.json"
MD_NAME = "transcript.md"


@dataclass(frozen=True)
class Segment:
    """One recognised span of speech."""

    start: float
    end: float
    text: str


@dataclass
class Transcript:
    """Result of transcribing one media file."""

    language: str | None
    language_probability: float | None
    duration: float | None
    model: str
    has_audio: bool = True
    segments: list[Segment] = field(default_factory=list)

    @property
    def text(self) -> str:
        """All segment texts joined by spaces."""
        return " ".join(s.text for s in self.segments).strip()

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly representation."""
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Transcript:
        """Inverse of :meth:`to_dict`."""
        return cls(
            language=d.get("language"),
            language_probability=d.get("language_probability"),
            duration=d.get("duration"),
            model=str(d.get("model", "")),
            has_audio=bool(d.get("has_audio", True)),
            segments=[Segment(float(s["start"]), float(s["end"]), str(s["text"]))
                      for s in d.get("segments", [])],
        )

    def to_markdown(self) -> str:
        """Markdown with one ``[mm:ss] text`` line per segment."""
        head = f"# Transcript\n\n- language: {self.language or 'unknown'}"
        if self.language_probability is not None:
            head += f" (p={self.language_probability:.2f})"
        head += f"\n- model: faster-whisper `{self.model}`\n\n"
        if not self.has_audio:
            return head + "_No audio track._\n"
        if not self.segments:
            return head + "_No speech detected._\n"
        return head + "\n".join(f"[{format_ts(s.start)}] {s.text}" for s in self.segments) + "\n"


def extract_audio(video: Path, wav: Path, *, timeout: float = 600) -> Path | None:
    """Write 16 kHz mono PCM WAV from ``video``; returns None if there is no audio stream."""
    if not probe(video).has_audio:
        return None
    wav.parent.mkdir(parents=True, exist_ok=True)
    run(
        "ffmpeg",
        ["-hide_banner", "-nostdin", "-y", *input_args(video), "-vn", "-sn", "-dn",
         "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", output_path(wav)],
        timeout=timeout,
        op="audio",
    )
    return wav


@lru_cache(maxsize=2)
def get_model(size: str, device: str = "cpu", compute_type: str = "int8") -> Any:
    """Load (once) and return a ``faster_whisper.WhisperModel``.

    A locally cached model is used without touching the network (faster-whisper otherwise
    queries the Hugging Face hub on every load); only if it is missing is it downloaded.
    """
    from faster_whisper import WhisperModel  # heavy import, deferred

    with span("media.whisper_load", model=size, device=device, compute_type=compute_type) as s:
        try:
            model = WhisperModel(size, device=device, compute_type=compute_type,
                                 local_files_only=True)
            s.set(source="cache")
        except Exception:  # not cached yet (huggingface_hub raises various types)
            model = WhisperModel(size, device=device, compute_type=compute_type)
            s.set(source="download")
        return model


def write_transcript(transcript: Transcript, out_dir: Path) -> tuple[Path, Path]:
    """Write ``transcript.json`` and ``transcript.md`` into ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    jp, mp = out_dir / JSON_NAME, out_dir / MD_NAME
    jp.write_text(json.dumps(transcript.to_dict(), indent=2, ensure_ascii=False), "utf-8")
    mp.write_text(transcript.to_markdown(), "utf-8")
    return jp, mp


def load_transcript(out_dir: Path) -> Transcript | None:
    """Load ``transcript.json`` from ``out_dir`` if present and valid."""
    jp = out_dir / JSON_NAME
    if not (jp.is_file() and (out_dir / MD_NAME).is_file()):
        return None
    try:
        return Transcript.from_dict(json.loads(jp.read_text("utf-8")))
    except (ValueError, KeyError, TypeError):
        return None


def transcribe(
    video: Path,
    out_dir: Path | None = None,
    *,
    model_size: str | None = None,
    device: str = "cpu",
    compute_type: str = "int8",
    language: str | None = None,
    beam_size: int = 5,
    no_repeat_ngram_size: int = 3,
    timeout: float = 600,
) -> Transcript:
    """Transcribe the speech in ``video`` (VAD on, language auto-detected unless given).

    Args:
        video: Media file with (or without) an audio stream.
        out_dir: If given, ``transcript.json`` and ``transcript.md`` are written there.
        model_size: faster-whisper model name; defaults to ``Settings.whisper_model``.
        device: ``cpu`` (default) or ``cuda``.
        compute_type: CTranslate2 compute type (``int8`` on CPU).
        language: ISO code to force a language; None to auto-detect.
        beam_size: Beam search width.
        no_repeat_ngram_size: Forbid repeating n-grams (0 disables). Together with
            ``condition_on_previous_text=False`` this stops the repetition loops Whisper
            falls into on music-heavy or non-English reels (measured 4x faster on one Hindi
            reel, identical text on an English one).
        timeout: ffmpeg audio-extraction timeout in seconds.
    """
    size = model_size or get_settings().whisper_model
    with span("media.transcribe", video=str(video), model=size) as s:
        with tempfile.TemporaryDirectory(prefix="heliograph-audio-") as tmp:
            wav = extract_audio(video, Path(tmp) / "audio.wav", timeout=timeout)
            if wav is None:
                result = Transcript(None, None, None, size, has_audio=False)
            else:
                model = get_model(size, device, compute_type)
                t0 = time.perf_counter()
                seg_iter, info = model.transcribe(
                    str(wav),
                    language=language,
                    beam_size=beam_size,
                    vad_filter=True,
                    condition_on_previous_text=False,
                    no_repeat_ngram_size=no_repeat_ngram_size,
                )
                segments = [
                    Segment(round(seg.start, 2), round(seg.end, 2), seg.text.strip())
                    for seg in seg_iter
                    if seg.text.strip()
                ]
                result = Transcript(
                    language=info.language,
                    language_probability=round(float(info.language_probability), 3),
                    duration=round(float(info.duration), 2),
                    model=size,
                    segments=segments,
                )
                s.set(asr_s=round(time.perf_counter() - t0, 2))
        s.set(
            has_audio=result.has_audio,
            language=result.language,
            segments=len(result.segments),
            chars=len(result.text),
        )
        if out_dir is not None:
            write_transcript(result, out_dir)
        return result
