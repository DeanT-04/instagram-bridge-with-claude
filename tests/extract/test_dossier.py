"""build_dossier tests with a synthetic reel (downloads are faked by copying a local video)."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from heliograph.extract import dossier as dossier_mod
from heliograph.extract.dossier import build_dossier, dossier_dir
from heliograph.instagram.models import Media
from heliograph.media.download import DownloadResult, check_url
from heliograph.media.ocr import OcrLine
from heliograph.media.transcribe import Segment, Transcript, write_transcript
from tests.media.videogen import make_text_video, make_video

REEL: dict[str, Any] = {
    "media": {
        "pk": "3700000000000000001",
        "id": "3700000000000000001_42",
        "code": "DZsynth01",
        "media_type": 2,
        "product_type": "clips",
        "taken_at": 1750000000,
        "caption": {"text": "Opening range breakout\n\n1. Mark the 5m high\n#trading"},
        "user": {"pk": "42", "username": "synthetic.trader", "full_name": "Syn Trader"},
        "video_versions": [
            {"url": "https://scontent.cdninstagram.com/o1/v/reel.mp4?sig=x", "width": 320,
             "height": 240}
        ],
        "video_duration": 4.0,
        "like_count": 12,
        "comment_count": 3,
        "play_count": 1000,
    }
}

PHOTO: dict[str, Any] = {
    "pk": "99", "id": "99_42", "code": "DZphoto1", "media_type": 1,
    "caption": {"text": "chart"},
    "user": {"username": "../../evil"},
    "image_versions2": {"candidates": [
        {"url": "https://scontent.cdninstagram.com/p.jpg", "width": 64, "height": 64}
    ]},
}


@pytest.fixture(scope="module")
def source_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return make_video(tmp_path_factory.mktemp("src") / "reel.mp4")


@pytest.fixture
def fake_download(
    monkeypatch: pytest.MonkeyPatch, source_video: Path, tmp_path: Path
) -> list[str]:
    """Replace the network download with a local copy; returns the list of requested URLs."""
    calls: list[str] = []
    image = tmp_path / "img.jpg"
    from PIL import Image

    Image.new("RGB", (64, 64), (200, 10, 10)).save(image)

    def fake(url: str, dest: Path, **_: object) -> DownloadResult:
        check_url(url)
        calls.append(url)
        src = image if ".jpg" in url else source_video
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        data = dest.read_bytes()
        ctype = "image/jpeg" if src is image else "video/mp4"
        return DownloadResult(dest, hashlib.sha256(data).hexdigest(), len(data), ctype,
                              "scontent.cdninstagram.com", 1)

    monkeypatch.setattr(dossier_mod, "download_sync", fake)
    return calls


@pytest.fixture
def fake_transcribe(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Replace Whisper with a canned transcript (the real one is covered in tests/media)."""
    calls: list[Path] = []

    def fake(video: Path, out_dir: Path | None = None, **_: object) -> Transcript:
        calls.append(video)
        t = Transcript("en", 0.97, 4.0, "fake",
                       segments=[Segment(0.5, 1.8, "Mark the high."), Segment(2.1, 3.9, "Wait.")])
        if out_dir is not None:
            write_transcript(t, out_dir)
        return t

    monkeypatch.setattr(dossier_mod, "transcribe", fake)
    return calls


def test_build_reel_dossier(
    tmp_path: Path, fake_download: list[str], fake_transcribe: list[Path]
) -> None:
    media = Media.from_api(REEL)
    d = build_dossier(media, tmp_path / "out")
    root = tmp_path / "out" / "synthetic.trader" / "DZsynth01"
    assert d.root == root
    for name in ("meta.json", "caption.md", "video.mp4", "transcript.json", "transcript.md",
                 "contact_sheet.jpg", "dossier.md", "frames/frames.json"):
        assert (root / name).is_file(), name
    assert len(d.frames) >= 2 and all(f.path.parent == root / "frames" for f in d.frames)
    meta = json.loads((root / "meta.json").read_text("utf-8"))
    assert meta["code"] == "DZsynth01" and "raw" not in meta
    assert "Opening range breakout" in (root / "caption.md").read_text("utf-8")

    md = (root / "dossier.md").read_text("utf-8")
    assert md.startswith("# @synthetic.trader (Syn Trader) - DZsynth01")
    assert "> 1. Mark the 5m high" in md
    assert "contact_sheet.jpg" in md
    assert "[00:00] frame #1** - `frames/frame_001_0000000ms.jpg`" in md
    # Speech and frames are interleaved in time order.
    assert md.index("[00:00] frame #1") < md.index("Mark the high.") < md.index("Wait.")
    assert "Speech language:** en" in md
    assert set(d.timings) >= {"download", "frames", "contact_sheet", "transcript", "total"}


def test_idempotent_and_force(
    tmp_path: Path, fake_download: list[str], fake_transcribe: list[Path]
) -> None:
    media = Media.from_api(REEL)
    build_dossier(media, tmp_path)
    assert len(fake_download) == 1 and len(fake_transcribe) == 1
    again = build_dossier(media, tmp_path)
    assert len(fake_download) == 1 and len(fake_transcribe) == 1
    assert set(again.skipped) == {"download", "frames", "contact_sheet", "transcript"}
    assert again.frames and again.transcript is not None and again.transcript.language == "en"
    build_dossier(media, tmp_path, force=True)
    assert len(fake_download) == 2 and len(fake_transcribe) == 2


def test_optional_stages(tmp_path: Path, fake_download: list[str]) -> None:
    d = build_dossier(Media.from_api(REEL), tmp_path, frames=False, transcript=False)
    assert d.video_path is not None and d.frames == [] and d.transcript is None
    assert not (d.root / "frames").exists() and not (d.root / "contact_sheet.jpg").exists()


def test_photo_dossier_and_safe_paths(tmp_path: Path, fake_download: list[str]) -> None:
    media = Media.from_api(PHOTO)
    assert dossier_dir(media, tmp_path) == tmp_path / "evil" / "DZphoto1"
    d = build_dossier(media, tmp_path)
    assert d.video_path is None and d.frames == []
    assert [p.name for p in d.images] == ["01.jpg"]
    assert "`images/01.jpg`" in d.markdown_path.read_text("utf-8")


def test_video_without_url_is_noted(tmp_path: Path, fake_download: list[str]) -> None:
    raw = json.loads(json.dumps(REEL))
    del raw["media"]["video_versions"]
    d = build_dossier(Media.from_api(raw), tmp_path)
    assert d.video_path is None and fake_download == []
    assert "no video_url" in d.markdown_path.read_text("utf-8")


@pytest.mark.slow
def test_real_transcription_end_to_end(tmp_path: Path, fake_download: list[str]) -> None:
    from heliograph.media.transcribe import get_model

    try:
        get_model("tiny")
    except Exception as exc:
        pytest.skip(f"whisper tiny model unavailable: {exc}")
    d = build_dossier(Media.from_api(REEL), tmp_path, whisper_model="tiny")
    assert d.transcript is not None and d.transcript.has_audio
    assert (d.root / "transcript.md").read_text("utf-8").startswith("# Transcript")


class FakeOcr:
    name = "fake-ocr"

    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.calls = 0

    def read(self, image: Path) -> list[OcrLine]:
        self.calls += 1
        return [OcrLine(t, 0.99, (0, 0, 10, 10)) for t in self.lines]


def test_ocr_signals_and_markdown(tmp_path: Path, fake_download: list[str],
                                  fake_transcribe: list[Path],
                                  monkeypatch: pytest.MonkeyPatch) -> None:
    engine = FakeOcr(["@synthetic.trader", "ENTRY WHEN CANDLE WICKS THRU VWAP", "EMA 9 | 5m"])
    monkeypatch.setattr(dossier_mod, "get_engine", lambda name=None: engine)
    d = build_dossier(Media.from_api(REEL), tmp_path)
    assert d.ocr is not None and d.ocr.engine == "fake-ocr"
    assert (d.root / "frames" / "ocr.json").is_file() and engine.calls == len(d.frames)
    md = d.markdown_path.read_text("utf-8")
    # OCR text sits under its frame, and repeated lines are only shown once.
    assert "frame #1** - `frames/frame_001_0000000ms.jpg`\n  - on screen: @synthetic.trader" in md
    assert md.count("ENTRY WHEN CANDLE WICKS THRU VWAP") == 1
    assert "## Detected terms" in md and "VWAP (screen)" in md and "5m (caption, screen)" in md
    assert "On-screen text:** OCR by fake-ocr" in md and "Possible mismatch" not in md
    assert "ocr" in d.timings

    again = build_dossier(Media.from_api(REEL), tmp_path)  # cached: no new OCR calls
    assert "ocr" in again.skipped and engine.calls == len(d.frames)
    build_dossier(Media.from_api(REEL), tmp_path, ocr=False, force=True)


def test_mismatch_flag_rendered(tmp_path: Path, fake_download: list[str],
                                monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(video: Path, out_dir: Path | None = None, **_: object) -> Transcript:
        t = Transcript("en", 0.97, 4.0, "fake", segments=[Segment(
            0.0, 3.9, "my morning routine: wake early, cold shower, gratitude journal, "
                      "meditation and a long walk with the dog before breakfast")])
        if out_dir is not None:
            write_transcript(t, out_dir)
        return t

    monkeypatch.setattr(dossier_mod, "transcribe", fake)
    raw = json.loads(json.dumps(REEL))
    raw["media"]["caption"]["text"] = ("Best XAUUSD scalping strategy on the 1 minute chart "
                                       "with the 50 EMA and RSI divergence")
    md = build_dossier(Media.from_api(raw), tmp_path).markdown_path.read_text("utf-8")
    assert "> **Possible mismatch:**" in md


def test_missing_ocr_engine_is_noted(tmp_path: Path, fake_download: list[str],
                                     fake_transcribe: list[Path],
                                     monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dossier_mod, "get_engine", lambda name=None: None)
    d = build_dossier(Media.from_api(REEL), tmp_path, ocr_engine="auto")
    assert d.ocr is None and any("uv sync --extra ocr" in n for n in d.notes)


def test_real_ocr_on_text_video(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                fake_transcribe: list[Path]) -> None:
    from heliograph.media.ocr import get_engine

    if get_engine("auto") is None:
        pytest.skip("no OCR engine installed (uv sync --extra ocr)")
    video = make_text_video(tmp_path / "slides.mp4",
                            ["ENTRY WHEN CANDLE WICKS THRU VWAP", "EMA 9 ON THE 5M CHART"])

    def fake_dl(url: str, dest: Path, **_: object) -> DownloadResult:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(video, dest)
        return DownloadResult(dest, "0", dest.stat().st_size, "video/mp4", "x", 1)

    monkeypatch.setattr(dossier_mod, "download_sync", fake_dl)
    d = build_dossier(Media.from_api(REEL), tmp_path / "out", ocr_engine="auto")
    assert d.ocr is not None and len(d.frames) >= 2
    squashed = d.ocr.text.upper().replace(" ", "")
    assert "CANDLEWICKS" in squashed and "VWAP" in squashed and "EMA9" in squashed
    md = d.markdown_path.read_text("utf-8")
    assert "on screen:" in md and "VWAP" in md


def test_translation_lines_in_timeline(tmp_path: Path, fake_download: list[str],
                                       monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(video: Path, out_dir: Path | None = None, **_: object) -> Transcript:
        t = Transcript("hi", 0.98, 4.0, "medium", upgraded_from="small",
                       segments=[Segment(0.5, 2.0, "पिवोट पॉइंट लगाना है")],
                       translation=[Segment(0.5, 2.0, "Put the pivot point standard.")])
        if out_dir is not None:
            write_transcript(t, out_dir)
        return t

    monkeypatch.setattr(dossier_mod, "transcribe", fake)
    d = build_dossier(Media.from_api(REEL), tmp_path)
    md = d.markdown_path.read_text("utf-8")
    assert "Speech language:** hi (model medium, English translation included)" in md
    assert md.index("पिवोट") < md.index("_(en)_ Put the pivot point standard.")
    assert "pivot point (speech)" in md  # terms are detected in the English translation
