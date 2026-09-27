"""Locate and summarise dossier folders (shared by the MCP extract tools and the CLI)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from heliograph.errors import HeliographError
from heliograph.instagram.endpoints import shortcode_from_url

__all__ = ["dossier_files", "find_dossier", "load_frames", "summarize_dossier"]

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_CODE = re.compile(r"[A-Za-z0-9_-]{1,64}")


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def find_dossier(ref: str, root: Path) -> Path:
    """Dossier folder for ``ref``: a folder path, a ``dossier.md`` path, a shortcode or a
    post URL. Paths must lie inside ``root`` (the configured dossier directory)."""
    text = ref.strip().strip('"')
    if not text:
        raise ValueError("dossier reference is empty")
    if "instagram.com" in text or _CODE.fullmatch(text):
        return _by_code(shortcode_from_url(text), root)
    candidate = Path(text).expanduser()
    if candidate.name == "dossier.md" or candidate.suffix in (".md", ".json"):
        candidate = candidate.parent
    if candidate.is_absolute() or len(candidate.parts) > 1:
        if not candidate.is_absolute():
            candidate = root / candidate
        if not _inside(candidate, root):
            raise HeliographError(f"{ref!r} is outside the dossier folder {root}",
                                  hint="Pass a shortcode or a path under the dossier folder.")
        if (candidate / "dossier.md").is_file() or (candidate / "meta.json").is_file():
            return candidate
        raise HeliographError(f"No dossier at {candidate}",
                              hint="Build it first with ig_extract_media.")
    raise ValueError(f"Not a shortcode, post URL or dossier path: {ref!r}")


def _by_code(code: str, root: Path) -> Path:
    if not _CODE.fullmatch(code):
        raise ValueError(f"Invalid shortcode {code!r}")
    matches = sorted(p for p in root.glob(f"*/{code}") if p.is_dir()) if root.is_dir() else []
    if not matches:
        raise HeliographError(f"No dossier for {code!r} under {root}",
                              hint="Build it first with ig_extract_media.")
    return matches[0]


def load_frames(folder: Path) -> list[dict[str, Any]]:
    """Frame index (``index``, ``time_s``, ``timestamp``, absolute ``path``) of a dossier."""
    index = folder / "frames" / "frames.json"
    if not index.is_file():
        return []
    try:
        items = json.loads(index.read_text(encoding="utf-8"))
    except ValueError:
        return []
    out: list[dict[str, Any]] = []
    for d in items if isinstance(items, list) else []:
        try:
            t = float(d["time_s"])
            path = (folder / "frames" / str(d["path"])).resolve()
        except (KeyError, TypeError, ValueError):
            continue
        m, s = divmod(int(max(0.0, t)), 60)
        out.append({"index": int(d.get("index", len(out) + 1)), "time_s": round(t, 2),
                    "timestamp": d.get("timestamp") or f"{m:02d}:{s:02d}", "path": str(path)})
    return out


def dossier_files(folder: Path) -> dict[str, Any]:
    """Which standard files exist in a dossier folder."""
    sheet = folder / "contact_sheet.jpg"
    images = sorted(p for p in (folder / "images").glob("*") if p.suffix.lower()
                    in _IMAGE_SUFFIXES) if (folder / "images").is_dir() else []
    return {
        "dossier_dir": str(folder),
        "dossier_md": str(folder / "dossier.md"),
        "meta": str(folder / "meta.json") if (folder / "meta.json").is_file() else None,
        "transcript_md": str(folder / "transcript.md")
        if (folder / "transcript.md").is_file() else None,
        "video": str(folder / "video.mp4") if (folder / "video.mp4").is_file() else None,
        "contact_sheet": str(sheet) if sheet.is_file() else None,
        "images": [str(p) for p in images],
    }


def summarize_dossier(folder: Path, *, md_chars: int | None = 20000) -> dict[str, Any]:
    """Paths, frame list and (trimmed) dossier.md text of a dossier folder."""
    md_path = folder / "dossier.md"
    text = md_path.read_text(encoding="utf-8") if md_path.is_file() else ""
    truncated = md_chars is not None and md_chars > 0 and len(text) > md_chars
    out: dict[str, Any] = {
        **{k: v for k, v in dossier_files(folder).items() if v not in (None, [])},
        "frames": [{k: f[k] for k in ("index", "timestamp", "time_s", "path")}
                   for f in load_frames(folder)],
        "dossier_md_text": text[:md_chars] if truncated and md_chars else text,
    }
    if truncated:
        out["dossier_md_truncated"] = True
    return out
