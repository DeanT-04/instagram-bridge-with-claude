"""Cheap text signals for a dossier: trading terms found in caption/transcript/OCR, and a
"possible mismatch" flag when the caption talks about something the video does not.

Everything here is regex / keyword heuristics - hints for the reader (Claude or a human),
not facts. Each detected term lists the sources it was seen in (``caption``, ``speech``,
``screen``) so a claim can be cross-checked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = ["Signals", "detect_terms", "find_signals", "keywords"]

_FX = "USD|EUR|JPY|GBP|CHF|CAD|AUD|NZD|USDT"
_NUM_WORDS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "ten": "10",
              "fifteen": "15", "thirty": "30", "sixty": "60"}
_TIMEFRAMES = {f"{n}m" for n in (1, 2, 3, 5, 10, 15, 20, 30, 45, 60, 65, 90, 120, 240)} | {
    f"{n}h" for n in (1, 2, 3, 4, 6, 8, 12)} | {"1D", "1W", "1H", "4H"}
_TF_UNITS = {"m": "m", "min": "m", "mins": "m", "minute": "m", "minutes": "m",
             "h": "h", "hr": "h", "hrs": "h", "hour": "h", "hours": "h", "hourly": "h"}

# (category, compiled pattern, normaliser). Patterns run on the original text; the
# normaliser maps a match to its display form.
_Rule = tuple[str, re.Pattern[str], str | None]
_I = re.IGNORECASE
_RULES: list[_Rule] = [
    ("tickers", re.compile(rf"\b(?:XAU|XAG|EUR|GBP|AUD|NZD|USD|BTC|ETH|SOL)(?:{_FX})\b"), None),
    ("tickers", re.compile(r"\b(?:BANK\s?NIFTY|NIFTY(?:\s?50)?|SENSEX|FINNIFTY|SPX|SPY|QQQ|"
                           r"NDX|NAS100|US100|US30|US500|GER40|DAX|DXY|VIX|TSLA|NVDA|AAPL|"
                           r"MNQ|MES|NQ|ES|YM|RTY|CL|GC)\b"), None),
    ("tickers", re.compile(r"(?<![\w$])\$[A-Z]{1,5}\b"), None),
    ("tickers", re.compile(r"\b(?:gold|bitcoin|crude oil|nasdaq|s&p ?500|dow jones)\b", _I),
     "lower"),
    ("indicators", re.compile(r"\b(?:\d{1,3}(?:\s?/\s?\d{1,3})*\s?)?(?:EMA|SMA|WMA|MA)(?![A-Za-z])"
                              r"(?:\s?\(?\d{1,3}(?:\s?/\s?\d{1,3})*\)?)?"), "upper"),
    ("indicators", re.compile(r"\b(?:VWAP|RSI|MACD|ATR|ADX|OBV|CCI|DMI|POC|VPOC|TPO)(?![A-Za-z])"
                              r"(?:\s?\(?\d{1,3}\)?)?", _I), "upper"),
    ("indicators", re.compile(r"\b(?:bollinger bands?|stochastic(?: rsi)?|ichimoku|"
                              r"supertrend|parabolic sar|fibonacci|fib(?: retracement)?|"
                              r"volume profile|pivot points?|moving average(?: ribbon)?|"
                              r"exponential moving average|order blocks?|fair value gaps?|"
                              r"FVG|break of structure|BOS|CHoCH|liquidity sweep|"
                              r"support|resistance|trend ?line|market structure)\b", _I),
     "lower"),
    ("timeframes", re.compile(r"\b(\d{1,3}|one|two|three|four|five|ten|fifteen|thirty|sixty)"
                              r"[\s-]?(m|mins?|minutes?|h|hrs?|hours?|hourly)\b(?!\w)", _I),
     "tf"),
    ("timeframes", re.compile(r"\b(?:daily|weekly|1D|1W|4H|1H)\b(?: chart| timeframe)?"),
     "tfword"),
    ("risk", re.compile(r"\b(?:R\s?:\s?R|RR|risk[\s-]to[\s-]reward|risk[\s/]reward|"
                        r"stop[\s-]?loss|take[\s-]?profit|trailing stop|breakeven|"
                        r"\d(?:\.\d)?\s?%\s?risk|1\s?:\s?[1-5](?:\.\d)?)\b", _I), "lower"),
    ("sessions", re.compile(r"\b(?:NY|New York|London|Asian?|Tokyo)\s(?:open|session|close)\b"
                            r"|\b(?:pre-?market|opening range|power hour|market open)\b", _I),
     "lower"),
    ("dates", re.compile(r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)?\s?\d{1,2}\s?"
                         r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s?'?\d{2,4}\b"),
     None),
]

_STOP = set("""
a about above after again all also am an and any are as at be because been before being
below between both but by can could did do does doing down during each few for from further
had has have having he her here hers him his how i if in into is it its just like me more
most my no nor not now of off on once only or other our out over own same she should so some
such than that the their them then there these they this those through to too under until
up very was we were what when where which while who whom why will with would you your
gonna wanna yeah okay right really thing things going get got make want know see look
comment dm link bio follow save share reel reels video trading trader trade trades
""".split())  # noqa: SIM905
_WORD = re.compile(r"[a-z][a-z0-9]{3,}")


def _norm(match: re.Match[str], how: str | None) -> str:
    text = re.sub(r"\s+", " ", match.group(0).strip())
    if how == "upper":
        return text.upper().replace(" (", "(").replace("( ", "(")
    if how == "lower":
        return text.lower()
    if how == "tf":
        n = _NUM_WORDS.get(match.group(1).lower(), match.group(1))
        return f"{n}{_TF_UNITS[match.group(2).lower()]}"
    if how == "tfword":
        word = match.group(0).split()[0]
        return {"daily": "1D", "weekly": "1W"}.get(word.lower(), word.upper())
    return text


def detect_terms(text: str) -> dict[str, set[str]]:
    """Category -> normalised terms found in ``text``."""
    out: dict[str, set[str]] = {}
    for cat, rx, how in _RULES:
        for m in rx.finditer(text):
            term = _norm(m, how)
            if cat == "timeframes" and term not in _TIMEFRAMES:
                continue  # "73m", "0m" etc. are OCR noise, not chart timeframes
            if term:
                out.setdefault(cat, set()).add(term)
    return out


def keywords(text: str) -> set[str]:
    """Content words (lower-case, 4+ chars, stop-words removed, crude plural folding)."""
    words = {w.rstrip("s") if len(w) > 4 else w for w in _WORD.findall(text.lower())}
    return words - _STOP


@dataclass
class Signals:
    """Detected terms (with sources) and the caption-vs-content mismatch verdict."""

    terms: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    shared_keywords: list[str] = field(default_factory=list)
    mismatch: str | None = None

    def to_markdown(self) -> list[str]:
        """Markdown lines for the "Detected" section (empty if nothing was found)."""
        lines: list[str] = []
        for cat in ("tickers", "timeframes", "indicators", "risk", "sessions", "dates"):
            items = self.terms.get(cat)
            if items:
                shown = [f"{t} ({', '.join(src)})" for t, src in sorted(items.items())][:25]
                lines.append(f"- **{cat.capitalize()}:** " + "; ".join(shown))
        return lines


def find_signals(caption: str, speech: str, screen: str) -> Signals:
    """Scan the three text sources; flag a possible mismatch when the caption's content
    words/terms barely overlap with what is said and shown."""
    sources = {"caption": caption, "speech": speech, "screen": screen}
    merged: dict[str, dict[str, list[str]]] = {}
    for name, text in sources.items():
        for cat, terms in detect_terms(text).items():
            for term in terms:
                merged.setdefault(cat, {}).setdefault(term, []).append(name)
    sig = Signals(terms=merged)
    cap_kw = keywords(re.sub(r"#\w+", " ", caption))
    body_kw = keywords(speech) | keywords(screen)
    sig.shared_keywords = sorted(cap_kw & body_kw)[:30]
    cap_terms = {t for c in merged.values() for t, src in c.items() if "caption" in src}
    body_terms = {t for c in merged.values() for t, src in c.items()
                  if {"speech", "screen"} & set(src)}
    if len(cap_kw) >= 5 and len(body_kw) >= 10:
        shared = len(sig.shared_keywords)
        ratio = shared / min(len(cap_kw), len(body_kw))
        if ratio < 0.08 and not (cap_terms & body_terms):
            sig.mismatch = (f"caption and speech/on-screen text share only {shared} of "
                            f"{len(cap_kw)} caption keywords and no trading terms")
        elif cap_terms and body_terms and not (cap_terms & body_terms) and ratio < 0.15:
            sig.mismatch = (f"caption terms ({', '.join(sorted(cap_terms)[:6])}) never appear "
                            "in the speech or on screen")
    return sig

