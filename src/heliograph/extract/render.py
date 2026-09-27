"""Render ``dossier.md``: metadata, caption, detected terms, and a merged timeline of
speech (plus English translation), keyframes and their on-screen text (OCR)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from heliograph.media.frames import format_ts

if TYPE_CHECKING:
    from heliograph.extract.dossier import Dossier

__all__ = ["render_markdown"]

OCR_CHARS = 400


def _rel(d: Dossier, p: Path) -> str:
    return p.relative_to(d.root).as_posix()


def _facts(d: Dossier) -> list[str]:
    m = d.media
    t = d.transcript
    lang = None
    if t and t.language:
        lang = t.language + (f" (model {t.model}" + (", English translation included"
                                                     if t.translation else "") + ")")
    facts = [
        ("URL", m.url),
        ("Type", m.media_type.value),
        ("Posted", m.taken_at.isoformat() if m.taken_at else None),
        ("Duration", f"{m.video_duration:.1f} s" if m.video_duration else None),
        ("Likes", m.like_count),
        ("Comments", m.comment_count),
        ("Plays", m.play_count),
        ("Speech language", lang),
        ("On-screen text", f"OCR by {d.ocr.engine}" if d.ocr else None),
    ]
    return [f"- **{k}:** {v}" for k, v in facts if v is not None]


def _screen_lines(d: Dossier) -> dict[int, str]:
    """Frame index -> new on-screen text (lines already shown on the previous frame are
    left out so recurring headers/watermarks do not repeat)."""
    if not d.ocr:
        return {}
    out: dict[int, str] = {}
    previous: set[str] = set()
    for f in sorted(d.ocr.frames, key=lambda f: f.index):
        texts = [ln.text for ln in f.lines]
        new = [t for t in texts if t not in previous]
        previous = set(texts)
        if new:
            text = " | ".join(new)
            out[f.index] = text if len(text) <= OCR_CHARS else text[:OCR_CHARS] + " ..."
    return out


def _timeline(d: Dossier) -> list[str]:
    screen = _screen_lines(d)
    events: list[tuple[float, int, str]] = []
    for f in d.frames:
        line = f"- **[{f.timestamp}] frame #{f.index}** - `{_rel(d, f.path)}`"
        if f.index in screen:
            line += f"\n  - on screen: {screen[f.index]}"
        events.append((f.time_s, 0, line))
    if d.transcript:
        events += [(s.start, 1, f"- [{format_ts(s.start)}] {s.text}")
                   for s in d.transcript.segments]
        events += [(s.start, 2, f"- [{format_ts(s.start)}] _(en)_ {s.text}")
                   for s in d.transcript.translation]
    lines = [e[2] for e in sorted(events, key=lambda e: (e[0], e[1]))] or ["_Nothing extracted._"]
    if d.transcript and not d.transcript.has_audio:
        lines.append("- _(video has no audio track)_")
    elif d.transcript and not d.transcript.segments:
        lines.append("- _(no speech detected)_")
    return lines


def render_markdown(d: Dossier) -> str:
    """The full ``dossier.md`` text for ``d``."""
    m = d.media
    owner = m.owner
    who = f"@{owner.username}" if owner else "unknown owner"
    if owner and owner.full_name:
        who += f" ({owner.full_name}{', verified' if owner.is_verified else ''})"
    lines = [f"# {who} - {m.code or m.id}", "", *_facts(d)]
    if d.signals and d.signals.mismatch:
        lines += ["", f"> **Possible mismatch:** {d.signals.mismatch}. The caption may belong "
                  "to a different video or be generic promo text - rely on speech and frames."]
    lines += ["", "## How to use this dossier", ""]
    if d.contact_sheet:
        lines += [
            f"View `{_rel(d, d.contact_sheet)}` first: all {len(d.frames)} keyframes on one "
            "image, each labelled `#n mm:ss`. Open individual frames for small text, or crop "
            "and zoom a region (`ig_view_frames(..., crop=...)` / "
            "`heliograph dossier frames <code> --crop`).",
            "",
        ]
    if d.ocr:
        lines += ["`on screen:` lines are machine OCR (only text new since the previous frame) "
                  "- verify numbers against the frame image; cross-check them with the "
                  "speech.", ""]
    others = ["`meta.json`", "`caption.md`"]
    others += ["`transcript.md`"] if d.transcript else []
    others += ["`frames/ocr.json`"] if d.ocr else []
    others += ["`video.mp4`"] if d.video_path else []
    lines += ["Other files: " + ", ".join(others) + ".", "", "## Caption", ""]
    caption = (m.caption or "").strip()
    lines += [f"> {ln}" if ln else ">" for ln in caption.splitlines()] if caption else ["_None._"]
    lines.append("")
    if d.signals:
        detected = d.signals.to_markdown()
        if detected:
            lines += ["## Detected terms (regex hints; source in brackets)", "", *detected, ""]
    if d.images:
        lines += ["## Images", "", *[f"- `{_rel(d, p)}`" for p in d.images], ""]
    if d.video_path:
        lines += ["## Timeline (speech + keyframes + on-screen text)", "", *_timeline(d), ""]
    if d.notes:
        lines += ["## Notes", "", *[f"- {n}" for n in d.notes], ""]
    return "\n".join(lines)
