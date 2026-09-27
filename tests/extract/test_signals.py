"""Regex term detection and the caption-vs-content mismatch heuristic."""

from __future__ import annotations

from heliograph.extract.signals import detect_terms, find_signals, keywords


def test_detect_terms() -> None:
    t = detect_terms("XAUUSD and NIFTY 50 on the 5m, 15 minute and 1H charts. EMA9, "
                     "9/21 EMA, RSI(14) and VWAP. R:R 1:2 with a stop loss. NY open. "
                     "$TSLA. five minute timeframe. Mon 25 Aug '25")
    assert {"XAUUSD", "NIFTY 50", "$TSLA"} <= t["tickers"]
    assert {"5m", "15m", "1H"} <= t["timeframes"]
    assert {"EMA9", "9/21 EMA", "RSI(14)", "VWAP"} <= t["indicators"]
    assert {"r:r", "1:2", "stop loss"} <= t["risk"]
    assert "ny open" in t["sessions"] and "Mon 25 Aug '25" in t["dates"]
    assert "5m" in detect_terms("Five minute timeframe")["timeframes"]
    assert detect_terms("24,078.30 73m 0m 12m 5m")["timeframes"] == {"5m"}  # OCR noise
    # No false positives from ordinary words.
    assert detect_terms("THEMA theme estimate mama 2000 times") == {}


def test_keywords() -> None:
    assert keywords("The VWAP strategy is simple, trading strategies!") == {
        "vwap", "strategy", "simple", "strategie"}


def test_no_mismatch_when_topics_overlap() -> None:
    sig = find_signals("VWAP for entries. EMA9 for exits. Simple strategy, tight risk.",
                       "Two indicators, VWAP and EMA. Five minute timeframe, exit on EMA 9.",
                       "CANDLE WICKS OR TAPS TOPSIDE OF VWAP = CALLS")
    assert sig.mismatch is None and "vwap" in sig.shared_keywords
    md = "\n".join(sig.to_markdown())
    assert "VWAP (caption, speech, screen)" in md and "5m (speech)" in md


def test_mismatch_flagged() -> None:
    sig = find_signals(
        "Best gold scalping strategy XAUUSD 1 minute EMA 50 #gold #forex",
        "Today I want to talk about my morning routine and how discipline changed my life "
        "and why you should wake up early every single day to succeed in business",
        "COMMENT GUIDE for the free ebook")
    assert sig.mismatch is not None and "share only 0" in sig.mismatch
    sig2 = find_signals("RSI divergence on BTCUSDT daily strategy explained simply here",
                        "we use the ichimoku cloud on the four hour chart and wait for the "
                        "tenkan cross above the kumo before entering the long position",
                        "")
    assert sig2.mismatch is not None
    # Too little text to judge: never flagged.
    assert find_signals("hi", "", "").mismatch is None
