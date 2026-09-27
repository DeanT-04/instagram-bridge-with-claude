# Security

Heliograph drives a real, logged-in Instagram session on your own machine and hands that capability to an AI agent. That deserves a clear threat model. This page summarises it; [ARCHITECTURE.md](ARCHITECTURE.md) has the implementation detail.

> Heliograph is v0.1.0. The controls below are implemented and covered by unit tests (security milestones 1 and 2). Write actions are dry-run tested but have **not yet been verified against a live account**.

## What we protect

| Asset | Where it lives |
|---|---|
| Your Instagram session (cookies, `sessionid`, `csrftoken`) | The dedicated browser profile at `~/.heliograph/browser-profile`, and your normal Edge profile used by the Store app |
| Your account's standing | Instagram's view of how the account behaves |
| Your data (dossiers, downloads, transcripts) | `~/.heliograph` and any output folder you choose |
| Observability logs | `~/.heliograph/eye/` |

## Threats and mitigations

| Threat | Mitigation |
|---|---|
| **Credential theft or leakage** | Heliograph never types, stores or logs a password. You log in to the dedicated profile once, by hand. The live-app driver reuses the app you are already signed into. |
| **Session tokens leaking into logs** | The Eye redacts cookies, `sessionid`, `csrftoken`, auth headers and token-like strings *before* anything is written to disk or exported. |
| **Another local process or website hijacking the browser** | The DevTools port is bound to `127.0.0.1` only, on a random free port recorded in `~/.heliograph/state.json`. It is never exposed on a network interface. |
| **An agent taking unwanted actions** (like, follow, comment, DM, post, unsave) | Every write tool (`ig_like`, `ig_follow`, `ig_comment`, `ig_send_dm`, ...) returns a dry run describing what *would* happen unless called with `confirm=true`, and Claude is instructed (`CLAUDE.md`) to show you that dry run and wait for your explicit yes. In the live app, `app_click` on action controls and `app_type` with `submit=true` are gated the same way, with control names normalised so look-alike labels can't slip past. Claude Code's own tool-permission prompts add a second gate. |
| **Prompt injection from Instagram content** (captions, comments, DMs, transcripts telling the agent to do something) | Content read from Instagram is data, not instructions. Write actions still need confirmation, and nothing Heliograph reads can grant that confirmation. Review what Claude proposes before approving. |
| **Account restrictions from automated-looking behaviour** | Reads and writes are rate-limited with jitter to stay human-paced. Use Heliograph on your own account only, and not for bulk actions on other people. |
| **Malicious URLs, downloads / SSRF** | Only Instagram URLs are accepted as input (strict allow-list). Media downloads are HTTPS-only and restricted to Instagram CDN hosts (`cdninstagram.com`, `fbcdn.net`); ffmpeg runs with a protocol whitelist. Writes are atomic. |
| **Path traversal** | Web-API paths and dossier folder names are validated (including Windows reserved names), so crafted shortcodes or usernames can't escape `~/.heliograph`. |
| **Attaching to someone else's browser** | Before connecting over CDP, Heliograph checks that the DevTools endpoint belongs to the browser it launched with its own profile. |
| **Local tampering** | Files under `~/.heliograph` are created with private permissions; external tools (ffmpeg, browsers) are resolved without trusting the current directory; `.env` is only loaded from trusted locations. |
| **Data leaving your machine** | Everything is local by default. The only optional outbound sink is Langfuse, enabled only when you set `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`, and redaction applies before export. |
| **Supply-chain risk** | Dependencies are pinned in `uv.lock`; `pip-audit` is part of the dev toolchain. |

## Out of scope

- A compromised operating system or user account. If malware runs as you, it can read your browser profiles with or without Heliograph.
- Instagram's own security and policies.
- Misuse against accounts you do not own. That is against both this project's intent and Instagram's terms.

## Your responsibilities

- Keep `~/.heliograph` private. It contains a logged-in browser profile. Do not sync it to cloud storage or commit it.
- Read what Claude asks to confirm before you approve it.
- Run `heliograph doctor` after updates, and keep Edge/Chrome current.

## Reporting a vulnerability

Please **do not open a public issue** for security problems.

1. Use GitHub's **private vulnerability reporting**: go to the repository's **Security** tab and choose **Report a vulnerability**.
2. Include the affected version or commit, reproduction steps, and the impact you expect.
3. Do **not** include real cookies, tokens or personal data. Redact them.

You should get an acknowledgement within 7 days. Fixes are released as quickly as practical, and reporters are credited unless they ask not to be.

## Supported versions

Only the latest commit on `main` is supported while the project is pre-1.0.
