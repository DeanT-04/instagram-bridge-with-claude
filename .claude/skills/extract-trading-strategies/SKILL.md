---
name: extract-trading-strategies
description: Extract every trading strategy from the user's saved Instagram collection (default "Trading strats") into structured Markdown notes. Use when the user asks to extract, summarise, catalogue or study the trading strategies/setups in their saved reels or posts.
---

# Extract trading strategies from a saved collection

Goal: one note per reel/post in `strategies/<creator>__<code>.md` using the template below,
plus an index `strategies/README.md`. You organise what creators *say and show*; you do not
judge or endorse it. Every performance claim (win rate, returns, "never loses") is the
creator's and **unverified** — label it that way. Nothing here is financial advice.

All steps are read-only on Instagram. Never like, save, follow or comment while doing this.

## 1. Find the collection

1. `ig_list_collections` — find the collection the user means (default **"Trading strats"**;
   match case-insensitively; if several are close, ask).
2. `ig_collection_posts(collection=<name or id>, limit=50)` — page with `next_cursor` until
   `has_more` is false. Note each item's `code`, `owner.username`, `url`, `type`.
3. Tell the user how many items there are and that you will work through them. If
   `strategies/README.md` exists, skip items already listed there unless asked to redo them.

## 2. Build dossiers

- Batch: `ig_extract_collection(collection, limit=5)` repeatedly (it skips finished dossiers,
  so it is resumable), or per item `ig_extract_media(ref=<code>)`.
- Items with `status: "error"`: retry once with `ig_extract_media`; if it still fails, record
  it in the index as "extraction failed" with the error, and move on. Use `eye_trace` on the
  trace id if the error is unclear.
- The first transcript can take minutes (Whisper model download). Photos/carousels have
  `images/` instead of frames and no transcript.

## 3. Study each dossier (one at a time)

1. `ig_read_dossier(<code>, include_transcript=true)` — read the caption, the merged
   `[mm:ss]` speech + keyframe timeline, and the transcript. Captions often hold the rules
   the video skips (or "comment X for the PDF" — note that, do not act on it).
2. `ig_view_frames(<code>)` — look at the contact sheet: which frames show charts,
   indicator panels, settings dialogs, entry/exit annotations, on-screen text?
3. `ig_view_frames(<code>, frames=[...], contact_sheet=false)` — open those frames
   individually (up to ~6 per call) to read exact numbers: indicator names and periods,
   timeframe labels, symbols, price levels, R:R boxes, drawn lines, text overlays.
4. Cross-check speech vs. visuals. When they disagree or something is unreadable, say so in
   Caveats rather than guessing. Never invent settings that were not stated or shown.
5. Not a strategy (motivation, ad, lifestyle, course pitch without rules)? Write a short note
   with Strategy name "No concrete strategy" and explain in Caveats.

## 4. Write the note

File name: `strategies/<creator>__<code>.md` (creator = Instagram username; replace any
character outside `A-Za-z0-9._-` with `_`). Use exactly this template:

```markdown
# <Strategy name (creator's name for it, or a short descriptive name)>

- **Creator:** @<username>
- **Source:** <post/reel URL> (posted <date>)
- **Dossier:** <dossier_dir>

## Market & timeframe
<instruments/asset class, session (e.g. NY open), chart timeframe(s); "not stated" if absent>

## Indicators & exact settings
| Indicator | Settings | Where seen |
|---|---|---|
| <e.g. RSI> | <e.g. length 14, levels 30/70, source close> | <[00:12] speech / frame #4> |

## Entry rules
1. <precise, testable condition> — evidence: <[mm:ss] / frame #n>

## Exit, stop & target rules
- **Stop:** <rule> — evidence: <...>
- **Target / take-profit:** <rule> — evidence: <...>
- **Other exits:** <time stop, trailing, opposite signal...>

## Risk management
<position size, % risk per trade, max trades/day, R:R; "not stated" if absent>

## Confluences & filters
- <trend filter, higher-timeframe bias, volume, news avoidance, time-of-day...>

## Evidence
| Time | Frame | What it shows / says |
|---|---|---|
| [00:05] | #2 | <chart with 9/21 EMA cross on 5m ES> |

## Caveats & unverified claims
- Claims such as "<quote>" are the creator's own and have not been verified.
- <ambiguities, unreadable settings, contradictions, survivorship/hindsight examples,
  missing rules needed to trade it, paid-course upsell>

## Source
<URL> — extracted by Heliograph on <date>. Not financial advice.
```

Keep rules concrete and testable ("close above the 20 EMA on the 5m chart"), cite a
timestamp or frame for each rule, and quote short phrases only.

## 5. Index

Create or update `strategies/README.md`:

```markdown
# Trading strategies — "<collection name>"

Extracted from saved Instagram posts. Creator claims are unverified. Not financial advice.

| # | Strategy | Creator | Market / TF | Key indicators | Note | Status |
|---|---|---|---|---|---|---|
| 1 | <name> | @<user> | <ES / 5m> | <RSI 14, EMA 9/21> | [note](<creator>__<code>.md) | done |
```

Status is `done`, `no strategy`, or `extraction failed: <reason>`. Finish by telling the user
how many notes were written, which items failed, and common themes across strategies.
