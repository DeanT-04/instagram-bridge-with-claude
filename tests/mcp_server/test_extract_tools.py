"""Dossier tools on a synthetic dossier (no downloads, ffmpeg or Whisper)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ImageContent, TextContent
from PIL import Image as PILImage

from heliograph.config import get_settings
from heliograph.errors import HeliographError
from heliograph.extract.dossier import Dossier
from heliograph.mcp.dossiers import find_dossier
from tests.mcp_server.conftest import REEL, Harness, json_of

CODE = REEL["code"]


@pytest.fixture
def dossier() -> Path:
    root = get_settings().dossier_path / "trader" / CODE
    frames = root / "frames"
    frames.mkdir(parents=True)
    index = []
    for i, t in enumerate((0.0, 4.5, 9.0), start=1):
        PILImage.new("RGB", (32, 18), (i * 60, 0, 0)).save(frames / f"frame_{i:03d}.jpg")
        index.append({"index": i, "time_s": t, "path": f"frame_{i:03d}.jpg", "phash": "0"})
    (frames / "frames.json").write_text(json.dumps(index), encoding="utf-8")
    PILImage.new("RGB", (64, 36), "white").save(root / "contact_sheet.jpg")
    (root / "dossier.md").write_text("# @trader - x\n\n## Caption\n\n> RSI 14 strategy\n",
                                     encoding="utf-8")
    (root / "meta.json").write_text("{}", encoding="utf-8")
    (root / "transcript.md").write_text("[00:01] buy when RSI < 30\n", encoding="utf-8")
    return root


def _blocks(result: Any) -> list[Any]:
    return list(result[0] if isinstance(result, tuple) else result)


async def test_view_frames_returns_contact_sheet_and_frames(harness: Harness,
                                                            dossier: Path) -> None:
    blocks = _blocks(await harness.server.call_tool(
        "ig_view_frames", {"dossier": CODE, "frames": [2, 3]}))
    assert isinstance(blocks[0], TextContent) and "frame #2 at 00:04" in blocks[0].text
    images = [b for b in blocks if isinstance(b, ImageContent)]
    assert len(images) == 3 and all(i.mimeType == "image/jpeg" and i.data for i in images)


async def test_view_frames_defaults_and_errors(harness: Harness, dossier: Path) -> None:
    blocks = _blocks(await harness.server.call_tool(
        "ig_view_frames", {"dossier": str(dossier / "dossier.md"), "contact_sheet": False,
                           "max_images": 2}))
    assert sum(isinstance(b, ImageContent) for b in blocks) == 2
    with pytest.raises(ToolError, match="No frame"):
        await harness.server.call_tool("ig_view_frames", {"dossier": CODE, "frames": [9]})
    with pytest.raises(ToolError, match="outside the dossier folder"):
        await harness.server.call_tool("ig_view_frames", {"dossier": str(dossier.parents[3])})
    with pytest.raises(ToolError, match="No dossier"):
        await harness.server.call_tool("ig_view_frames", {"dossier": "NOPE123"})


async def test_read_dossier(harness: Harness, dossier: Path) -> None:
    out = json_of(await harness.server.call_tool(
        "ig_read_dossier", {"dossier": f"https://www.instagram.com/reel/{CODE}/",
                            "include_transcript": True}))
    assert "RSI 14" in out["dossier_md_text"] and "RSI < 30" in out["transcript_md_text"]
    assert [f["timestamp"] for f in out["frames"]] == ["00:00", "00:04", "00:09"]
    assert out["contact_sheet"].endswith("contact_sheet.jpg")


def test_find_dossier_rejects_bad_refs(dossier: Path) -> None:
    root = get_settings().dossier_path
    assert find_dossier(CODE, root) == dossier
    with pytest.raises(HeliographError, match="outside"):
        find_dossier("../..", root)
    with pytest.raises(ValueError):
        find_dossier("", root)


async def test_extract_media_and_collection(harness: Harness, dossier: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    pk = REEL["pk"]
    harness.api.routes[f"/api/v1/media/{pk}/info/"] = {"items": [REEL]}
    built: list[str] = []

    def fake_build(media: Any, out_root: Path, **kw: Any) -> Dossier:
        built.append(media.code)
        root = out_root / "trader" / media.code
        return Dossier(root=root, media=media, meta_path=root / "meta.json",
                       caption_path=root / "caption.md", markdown_path=root / "dossier.md")

    monkeypatch.setattr("heliograph.extract.dossier.build_dossier", fake_build)
    out = json_of(await harness.server.call_tool("ig_extract_media", {"ref": pk}))
    assert out["dossier_md"].endswith("dossier.md") and len(out["frames"]) == 3
    assert built == [CODE]

    out = json_of(await harness.server.call_tool(
        "ig_extract_collection", {"collection": "Trading strats", "limit": 5}))
    # the fixture reel belongs to trader.joe (not "trader"): nothing exists yet -> built
    assert out["processed"] == 2 and out["built"] == 2 and out["error"] == 0


async def test_view_frames_crop_and_ocr_text(harness: Harness, dossier: Path) -> None:
    (dossier / "frames" / "ocr.json").write_text(json.dumps({
        "engine": "fake", "elapsed_s": 0.1, "frames": [
            {"index": 2, "time_s": 4.5, "path": "frame_002.jpg",
             "lines": [{"text": "EMA 9", "score": 0.9, "box": None}]}]}), encoding="utf-8")
    blocks = _blocks(await harness.server.call_tool(
        "ig_view_frames", {"dossier": CODE, "frames": [2, 3], "crop": "top-left",
                           "scale": 3}))
    assert isinstance(blocks[0], TextContent) and "frame #2 crop (0, 0, 16, 9) x3" in \
        blocks[0].text
    assert sum(isinstance(b, ImageContent) for b in blocks) == 2
    assert len(list((dossier / "crops").glob("*.png"))) == 2
    with pytest.raises(ToolError, match="crop needs"):
        await harness.server.call_tool("ig_view_frames", {"dossier": CODE, "crop": "top"})
    with pytest.raises(ToolError, match="Unknown crop"):
        await harness.server.call_tool("ig_view_frames",
                                       {"dossier": CODE, "frames": [1], "crop": "nowhere"})
    out = json_of(await harness.server.call_tool("ig_read_dossier", {"dossier": CODE}))
    assert out["frames"][1]["text"] == "EMA 9" and "text" not in out["frames"][0]
    assert out["ocr"].endswith("ocr.json")
