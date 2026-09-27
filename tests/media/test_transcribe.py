"""Transcription tests (audio extraction always; Whisper 'tiny' only when available)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from heliograph.media import transcribe as transcribe_mod
from heliograph.media.ffmpeg import probe
from heliograph.media.transcribe import (
    Segment,
    Transcript,
    extract_audio,
    load_transcript,
    transcribe,
    write_transcript,
)


def test_extract_audio(video: Path, silent_video: Path, tmp_path: Path) -> None:
    wav = extract_audio(video, tmp_path / "a.wav")
    assert wav is not None
    info = probe(wav)
    assert info.has_audio and not info.has_video
    assert info.duration is not None and 3.5 < info.duration < 4.5
    assert extract_audio(silent_video, tmp_path / "b.wav") is None


def test_no_audio_is_graceful(silent_video: Path, tmp_path: Path) -> None:
    t = transcribe(silent_video, tmp_path, model_size="tiny")
    assert not t.has_audio and t.segments == [] and t.language is None
    assert "No audio" in (tmp_path / "transcript.md").read_text("utf-8")


def test_markdown_and_roundtrip(tmp_path: Path) -> None:
    t = Transcript(
        "en", 0.99, 70.0, "small",
        segments=[Segment(0.0, 2.0, "Buy the dip."), Segment(65.2, 69.0, "Not advice.")],
    )
    write_transcript(t, tmp_path)
    md = (tmp_path / "transcript.md").read_text("utf-8")
    assert "[00:00] Buy the dip." in md and "[01:05] Not advice." in md
    assert json.loads((tmp_path / "transcript.json").read_text("utf-8"))["language"] == "en"
    assert load_transcript(tmp_path) == t


@pytest.mark.slow
def test_transcribe_tiny_model(video: Path, tmp_path: Path) -> None:
    try:
        transcribe_mod.get_model("tiny")
    except Exception as exc:  # model not cached and no network, etc.
        pytest.skip(f"whisper tiny model unavailable: {exc}")
    t = transcribe(video, tmp_path, model_size="tiny")
    assert t.has_audio and t.model == "tiny"
    assert t.duration is not None and 3.5 < t.duration < 4.5
    assert (tmp_path / "transcript.json").is_file() and (tmp_path / "transcript.md").is_file()
    for seg in t.segments:
        assert 0 <= seg.start <= seg.end <= 4.5
    if not t.segments:
        pytest.skip("no speech synthesised (ffmpeg without flite): nothing to compare")
    assert t.language == "en"
    assert "hello" in t.text.lower()
