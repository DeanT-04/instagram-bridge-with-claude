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
    split_words,
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


def test_split_words_caps_length_and_breaks_at_sentences() -> None:
    words = [(i * 0.5, i * 0.5 + 0.4, f" w{i}") for i in range(40)]  # 20 s of speech
    segs = split_words(words, max_len=8.0)
    assert all(s.end - s.start <= 8.0 for s in segs) and len(segs) == 3
    assert " ".join(s.text for s in segs) == " ".join(f"w{i}" for i in range(40))
    punct = [(0.0, 0.5, " Buy"), (0.5, 3.0, " now."), (3.2, 3.6, " Then"), (3.6, 4.0, " exit.")]
    assert [s.text for s in split_words(punct, max_len=8.0)] == ["Buy now.", "Then exit."]
    assert split_words([]) == []


class _Word:
    def __init__(self, start: float, end: float, word: str) -> None:
        self.start, self.end, self.word = start, end, word


class _Seg:
    def __init__(self, words: list[_Word]) -> None:
        self.words = words
        self.start, self.end = words[0].start, words[-1].end
        self.text = "".join(w.word for w in words)


class _Info:
    def __init__(self, language: str) -> None:
        self.language, self.language_probability, self.duration = language, 0.98, 4.0


class FakeModel:
    def __init__(self, name: str, language: str, calls: list[tuple[str, str]]) -> None:
        self.name, self.language, self.calls = name, language, calls

    def transcribe(self, wav: str, **kw: object) -> tuple[object, _Info]:
        task = str(kw.get("task", "transcribe"))
        self.calls.append((self.name, task))
        assert kw.get("word_timestamps") is True

        def gen() -> object:
            text = " hello" if task == "translate" else " namaste"
            yield _Seg([_Word(i * 1.0, i * 1.0 + 0.9, text) for i in range(12)])

        return gen(), _Info(self.language)


@pytest.mark.parametrize("language", ["hi", "en"])
def test_non_english_upgrade_and_translation(video: Path, tmp_path: Path, language: str,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(transcribe_mod, "get_model",
                        lambda size, *a: FakeModel(size, language, calls))
    t = transcribe(video, tmp_path, model_size="small", non_english_model="medium",
                   translate=True)
    assert all(s.end - s.start <= 8.0 for s in t.segments) and len(t.segments) == 2
    if language == "hi":
        assert calls == [("small", "transcribe"), ("medium", "transcribe"),
                         ("medium", "translate")]
        assert t.model == "medium" and t.upgraded_from == "small"
        assert t.translation and t.english_text.startswith("hello")
        assert set(t.timings) == {"asr_s", "translate_s"}
        md = (tmp_path / "transcript.md").read_text("utf-8")
        assert "auto-upgraded from `small`" in md and "## English translation" in md
        assert load_transcript(tmp_path) == t
    else:
        assert calls == [("small", "transcribe")] and t.translation == []
        assert t.model == "small" and t.upgraded_from is None


def test_no_upgrade_to_smaller_model(video: Path, tmp_path: Path,
                                     monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(transcribe_mod, "get_model",
                        lambda size, *a: FakeModel(size, "hi", calls))
    t = transcribe(video, None, model_size="large-v3", non_english_model="medium",
                   translate=False)
    assert calls == [("large-v3", "transcribe")] and t.model == "large-v3"
