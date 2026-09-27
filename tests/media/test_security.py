"""Security regressions for the media pipeline and dossier paths."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from heliograph.extract.dossier import dossier_dir
from heliograph.instagram.models import Media
from heliograph.media import ffmpeg
from heliograph.media.frames import INDEX_NAME, load_index


def test_input_args_whitelist_file_protocol_and_absolute_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    args = ffmpeg.input_args(Path("-evil.mp4"))
    assert args[:3] == ["-protocol_whitelist", "file", "-i"]
    assert Path(args[3]).is_absolute() and not args[3].startswith("-")
    assert Path(ffmpeg.output_path(Path("concat:x.jpg"))).is_absolute()


def test_protocol_whitelist_blocks_playlist_ssrf(tmp_path: Path) -> None:
    if ffmpeg.shutil.which("ffprobe") is None:
        pytest.skip("ffprobe not on PATH")
    playlist = tmp_path / "video.mp4"
    playlist.write_text("#EXTM3U\n#EXTINF:1,\nhttp://127.0.0.1:9/secret.ts\n#EXT-X-ENDLIST\n")
    with pytest.raises(ffmpeg.FFmpegError, match="whitelist"):
        ffmpeg.probe(playlist, timeout=30)


def test_load_index_refuses_paths_outside_folder(tmp_path: Path) -> None:
    outside = tmp_path / "secret.jpg"
    outside.write_bytes(b"x")
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    (frames_dir / INDEX_NAME).write_text(json.dumps(
        [{"index": 1, "time_s": 0, "path": "../secret.jpg", "phash": "0"}]))
    assert load_index(frames_dir) is None
    (frames_dir / INDEX_NAME).write_text(json.dumps(
        [{"index": 1, "time_s": 0, "path": str(outside), "phash": "0"}]))
    assert load_index(frames_dir) is None


@pytest.mark.parametrize(
    ("username", "code", "owner_dir", "code_dir"),
    [
        ("con", "NUL", "_con", "_NUL"),
        ("aux.backup", "com1", "_aux.backup", "_com1"),
        ("..", "../../x", "unknown", "x"),
        ("a:b", "c\\d", "a_b", "c_d"),
        ("name.", ".hidden.", "name", "hidden"),
        ("lpt9", "abc", "_lpt9", "abc"),
        ("console", "code", "console", "code"),
    ],
)
def test_dossier_dir_sanitises_hostile_names(
    tmp_path: Path, username: str, code: str, owner_dir: str, code_dir: str
) -> None:
    media = Media.from_api({"pk": "1", "code": code, "media_type": 2,
                            "user": {"pk": "2", "username": username}})
    path = dossier_dir(media, tmp_path)
    assert path == tmp_path / owner_dir / code_dir
    assert path.resolve().is_relative_to(tmp_path.resolve())
