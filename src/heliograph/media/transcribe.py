"""Speech-to-text with faster-whisper (CPU, int8), producing timestamped segments.

Audio is first extracted with ffmpeg to 16 kHz mono WAV. Videos without an audio stream
yield an empty :class:`Transcript` with ``has_audio=False`` rather than an error. Models
are loaded lazily and cached per ``(size, device, compute_type)``; the default size comes
from ``Settings.whisper_model``.

* Word timestamps are on and segments are re-split at word boundaries into chunks of at
  most ``max_segment_s`` (8 s), so each line of speech lines up with nearby keyframes
  (Whisper alone emits 30 s segments on music-heavy reels).
* When the detected language is not English, ``Settings.whisper_model_non_english``
  (default ``medium``) replaces a smaller model, and with ``Settings.whisper_translate`` an
  English translation (Whisper ``task="translate"``) is stored next to the original.
"""

from __future__ import annotations

import json
import re
import tempfile
import time
from collections.abc import Iterable
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
    "split_words",
    "transcribe",
    "write_transcript",
]

JSON_NAME = "transcript.json"
MD_NAME = "transcript.md"
MAX_SEGMENT_S = 8.0
_ORDER = ["tiny", "base", "small", "medium", "large"]
_SENTENCE_END = re.compile(r"[.!?।॥。？！]$")


@dataclass(frozen=True)
class Segment:
    """One recognised span of speech."""

    start: float
    end: float
    text: str


def _segments(items: Iterable[dict[str, Any]]) -> list[Segment]:
    return [Segment(float(s["start"]), float(s["end"]), str(s["text"])) for s in items]


def _lines(segments: Iterable[Segment]) -> str:
    return "\n".join(f"[{format_ts(s.start)}] {s.text}" for s in segments) + "\n"


@dataclass
class Transcript:
    """Result of transcribing one media file."""

    language: str | None
    language_probability: float | None
    duration: float | None
    model: str
    has_audio: bool = True
    segments: list[Segment] = field(default_factory=list)
    translation: list[Segment] = field(default_factory=list)
    upgraded_from: str | None = None
    timings: dict[str, float] = field(default_factory=dict)

    @property
    def text(self) -> str:
        """All segment texts joined by spaces."""
        return " ".join(s.text for s in self.segments).strip()

    @property
    def english_text(self) -> str:
        """The English translation when there is one, else :attr:`text`."""
        return " ".join(s.text for s in self.translation).strip() or self.text

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
            segments=_segments(d.get("segments", [])),
            translation=_segments(d.get("translation", [])),
            upgraded_from=d.get("upgraded_from"),
            timings={str(k): float(v) for k, v in (d.get("timings") or {}).items()},
        )

    def to_markdown(self) -> str:
        """Markdown with one ``[mm:ss] text`` line per segment (+ translation section)."""
        head = f"# Transcript\n\n- language: {self.language or 'unknown'}"
        if self.language_probability is not None:
            head += f" (p={self.language_probability:.2f})"
        head += f"\n- model: faster-whisper `{self.model}`"
        if self.upgraded_from:
            head += f" (auto-upgraded from `{self.upgraded_from}` for non-English speech)"
        head += "\n\n"
        if not self.has_audio:
            return head + "_No audio track._\n"
        if not self.segments:
            return head + "_No speech detected._\n"
        body = _lines(self.segments)
        if self.translation:
            body += "\n## English translation (Whisper)\n\n" + _lines(self.translation)
        return head + body


def split_words(words: Iterable[tuple[float, float, str]], *,
                max_len: float = MAX_SEGMENT_S) -> list[Segment]:
    """Group Whisper words ``(start, end, text)`` into segments of at most ``max_len``
    seconds (a single longer word stays whole), also breaking after sentence punctuation
    once a chunk is at least ``max_len / 3`` long. Word texts carry their own leading
    spaces (Whisper convention)."""
    out: list[Segment] = []
    chunk: list[tuple[float, float, str]] = []

    def flush() -> None:
        text = "".join(w[2] for w in chunk).strip()
        if text:
            out.append(Segment(round(chunk[0][0], 2), round(chunk[-1][1], 2), text))
        chunk.clear()

    for w in words:
        if chunk and w[1] - chunk[0][0] > max_len:
            flush()
        chunk.append(w)
        if _SENTENCE_END.search(w[2].strip()) and w[1] - chunk[0][0] >= max_len / 3:
            flush()
    if chunk:
        flush()
    return out


def _resplit(seg_iter: Iterable[Any], max_len: float) -> list[Segment]:
    out: list[Segment] = []
    for seg in seg_iter:
        words = getattr(seg, "words", None)
        if words:
            out += split_words(((w.start, w.end, w.word) for w in words), max_len=max_len)
        elif seg.text.strip():
            out.append(Segment(round(seg.start, 2), round(seg.end, 2), seg.text.strip()))
    return out


def _rank(name: str) -> int:
    base = name.removesuffix(".en").removeprefix("distil-").split("-")[0]
    return _ORDER.index(base) if base in _ORDER else -1


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
    non_english_model: str | None = None,
    translate: bool | None = None,
    device: str = "cpu",
    compute_type: str = "int8",
    language: str | None = None,
    beam_size: int = 5,
    no_repeat_ngram_size: int = 3,
    max_segment_s: float = MAX_SEGMENT_S,
    timeout: float = 600,
) -> Transcript:
    """Transcribe the speech in ``video`` (VAD on, language auto-detected unless given).

    Args:
        video: Media file with (or without) an audio stream.
        out_dir: If given, ``transcript.json`` and ``transcript.md`` are written there.
        model_size: faster-whisper model name; defaults to ``Settings.whisper_model``.
        non_english_model: Model to switch to when the detected language is not English
            and it is bigger than ``model_size``; defaults to
            ``Settings.whisper_model_non_english`` ("" disables the upgrade).
        translate: Also translate non-English speech to English; defaults to
            ``Settings.whisper_translate``.
        device: ``cpu`` (default) or ``cuda``.
        compute_type: CTranslate2 compute type (``int8`` on CPU).
        language: ISO code to force a language; None to auto-detect.
        beam_size: Beam search width.
        no_repeat_ngram_size: Forbid repeating n-grams (0 disables). Together with
            ``condition_on_previous_text=False`` this stops the repetition loops Whisper
            falls into on music-heavy or non-English reels.
        max_segment_s: Re-split segments (by word timestamps) to at most this long.
        timeout: ffmpeg audio-extraction timeout in seconds.
    """
    settings = get_settings()
    size = model_size or settings.whisper_model
    upgrade = settings.whisper_model_non_english if non_english_model is None \
        else non_english_model
    do_translate = settings.whisper_translate if translate is None else translate
    opts: dict[str, Any] = dict(beam_size=beam_size, vad_filter=True,
                                condition_on_previous_text=False,
                                no_repeat_ngram_size=no_repeat_ngram_size, word_timestamps=True)
    with span("media.transcribe", video=str(video), model=size) as s:
        with tempfile.TemporaryDirectory(prefix="heliograph-audio-") as tmp:
            wav = extract_audio(video, Path(tmp) / "audio.wav", timeout=timeout)
            if wav is None:
                result = Transcript(None, None, None, size, has_audio=False)
            else:
                t0 = time.perf_counter()
                seg_iter, info = get_model(size, device, compute_type).transcribe(
                    str(wav), language=language, **opts)
                lang, used, upgraded_from = info.language, size, None
                if lang != "en" and upgrade and _rank(upgrade) > _rank(size):
                    # The generator is lazy: nothing was decoded with the small model yet.
                    used, upgraded_from = upgrade, size
                    seg_iter, info = get_model(used, device, compute_type).transcribe(
                        str(wav), language=lang, **opts)
                segments = _resplit(seg_iter, max_segment_s)
                timings = {"asr_s": round(time.perf_counter() - t0, 2)}
                translation: list[Segment] = []
                if do_translate and lang != "en" and segments:
                    t1 = time.perf_counter()
                    tr_iter, _ = get_model(used, device, compute_type).transcribe(
                        str(wav), language=lang, task="translate", **opts)
                    translation = _resplit(tr_iter, max_segment_s)
                    timings["translate_s"] = round(time.perf_counter() - t1, 2)
                result = Transcript(
                    language=lang,
                    language_probability=round(float(info.language_probability), 3),
                    duration=round(float(info.duration), 2),
                    model=used,
                    segments=segments,
                    translation=translation,
                    upgraded_from=upgraded_from,
                    timings=timings,
                )
                s.set(**timings, upgraded_from=upgraded_from)
        s.set(
            has_audio=result.has_audio,
            language=result.language,
            segments=len(result.segments),
            translated=len(result.translation),
            chars=len(result.text),
        )
        if out_dir is not None:
            write_transcript(result, out_dir)
        return result
