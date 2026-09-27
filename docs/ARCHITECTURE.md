# Heliograph — Architecture

Heliograph links Claude (via MCP) to Instagram on the user's own device.
Python 3.12, `uv`-managed, `src/` layout, package name `heliograph`.

## Findings that shaped the design (verified on Windows 11, Edge 154)

- The Microsoft Store "Instagram" app (`Facebook.InstagramBeta`) is an **Edge PWA**
  (`--app-id=akpamiohjfcnimfljfndmaldlcfphjmp`, Edge `Default` profile).
- Edge ≥136 **ignores `--remote-debugging-port` on the default user-data-dir**, so
  DevTools cannot attach to the installed app. (Tested: port never opens.)
- The installed app's window **is fully readable/drivable via Windows UI Automation**:
  the web document (`DocumentControl` named after the page title) exposes buttons,
  links, captions, like/comment/save controls. ~260 nodes walk in ~0.6 s.

## Two drivers, one interface

| Driver | Target | Strength | Used for |
|---|---|---|---|
| `uia` (`drivers/uia`) | The installed Store app window | Real app, user's real session, zero login | Navigation, reading what's on screen, clicking like/save/follow, screenshots |
| `cdp` (`drivers/cdp`) | A dedicated Edge/Chrome profile at `~/.heliograph/browser-profile`, launched as an app window (`--app=https://www.instagram.com/`) with `--remote-debugging-port` on 127.0.0.1 | Structured JSON via Instagram's web API (same-origin `fetch` inside the page), video URLs, network capture | Saved collections, reel metadata, downloads, bulk extraction |

User logs in to the CDP profile **once, manually** (Heliograph never sees or stores a password).
Both drivers implement `drivers/base.py::InstagramDriver` (Protocol) where they overlap.

## Package layout

```
src/heliograph/
  __init__.py            version
  config.py              pydantic-settings; env prefix HELIOGRAPH_; paths under ~/.heliograph
  errors.py              HeliographError hierarchy (NotLoggedIn, AppNotInstalled, RateLimited, ...)
  eye/                   the "background eye" — observability (see below)
  detect/                environment detection: Store app, Edge/Chrome path, ffmpeg, OS, login state
  drivers/
    base.py              InstagramDriver Protocol + shared dataclasses
    uia/                 Windows UI Automation driver (window, tree snapshot, actions)
    cdp/                 browser launcher, CDP session (Playwright connect_over_cdp), web-API client
  instagram/
    models.py            pydantic: User, Media, MediaType, Collection, Comment, Page[T]
    service.py           high-level ops composed from drivers (feed, profile, saved, search, DMs...)
  media/
    download.py          allow-listed CDN download (cdninstagram.com, fbcdn.net only), atomic writes
    frames.py            ffmpeg scene-change + interval keyframes, perceptual dedupe
    transcribe.py        faster-whisper (CPU int8), timestamped segments
  extract/
    dossier.py           reel -> dossier folder: meta.json, video.mp4, transcript.{json,md}, frames/*.jpg, dossier.md
  mcp/
    server.py            FastMCP server exposing tools to Claude
  cli.py                 Typer CLI: setup, doctor, login, eye, mcp, extract
tests/                   pytest; unit tests use fixtures/fakes, live tests marked `@pytest.mark.live`
docs/                    ARCHITECTURE.md, i18n READMEs (docs/i18n/README.<lang>.md), SECURITY.md
scripts/                 setup.ps1 (Windows), setup.sh (macOS/Linux) — one-command bootstrap
.claude/skills/          project skills (e.g. extract-trading-strategies)
.mcp.json                registers the Heliograph MCP server for Claude Code on clone
```

## The Eye (observability) — `heliograph.eye`

Local-first; no external service required.
- `eye.span(name, **attrs)` context manager + `@eye.traced` decorator (sync & async) record
  start/end/duration/status/error+traceback into **`~/.heliograph/eye/events.jsonl`** (rotating)
  and an SQLite index `eye.db` for querying.
- Every MCP tool call, driver action, HTTP request, subprocess (ffmpeg/whisper) is a span with a
  `trace_id` so one Claude request can be followed end to end.
- Automatic failure artifacts: on error in a UI/CDP span, a screenshot + accessibility/DOM snapshot
  is saved (owner-only `eye/artifacts/`) and linked from the event; artifacts older than
  `artifact_retention_days` (default 7) are pruned when the eye starts.
- Redaction: cookies, `sessionid`, `csrftoken`, auth headers, and anything matching token patterns
  are scrubbed before writing.
- `heliograph eye` — live tail with colours + rolling health (error rate, p95 latency, top failing ops).
- `heliograph eye report` / MCP tool `eye_report` — Claude can read recent errors/anomalies itself.
- Optional sink: if `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` are set, spans are also exported
  (off by default).

## Safety rules (enforced in code)

- Write actions (like, follow, comment, DM, post, unsave) require `confirm=True` at the MCP layer
  and are rate-limited with jitter; reads are rate-limited too. The write limit is shared by
  every Heliograph process and both drivers (`~/.heliograph/ratelimit.json` under a lock file,
  `heliograph.writelimit`).
- Live app: Enter (any modifiers) or a newline sent to a comment/message box, and Enter/Space on a
  focused write control, need `confirm=True` (driver `press`/`type_text` and MCP `app_type`);
  the mouse centre-click fallback refuses container-sized elements (>400x200 px).
- Downloads only from allow-listed Instagram CDN hosts over HTTPS.
- DevTools port bound to 127.0.0.1, random free port, recorded in `~/.heliograph/state.json`.
- No credentials are ever typed, stored or logged by Heliograph.
