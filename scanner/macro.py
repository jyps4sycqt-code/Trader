"""Macro regime read.

No FRED key — pull what we can from yfinance:
  ^VIX  : volatility index
  ^TNX  : 10-year Treasury yield (x10, e.g. 4.2 -> 42.0)
  ^IRX  : 13-week T-bill yield (x10)
  ^GSPC : S&P 500 (for trend regime)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import yfinance as yf

log = logging.getLogger(__name__)


@dataclass
class MacroSnapshot:
    vix: float | None
    ten_year: float | None
    three_month: float | None
    spx_above_200dma: bool | None
    regime: str                # "normal" | "defensive" | "skip"
    notes: list[str]


def read_macro(cfg: dict) -> MacroSnapshot:
    notes: list[str] = []
    vix = _last_close("^VIX")
    tnx = _last_close("^TNX")
    irx = _last_close("^IRX")
    spx_above = _spx_above_200dma()

    # Treasury indices on yfinance are reported x10 of percent.
    ten_year = (tnx / 10.0) if tnx is not None else None
    three_month = (irx / 10.0) if irx is not None else None

    regime = "normal"
    if vix is not None:
        if vix >= cfg["macro"]["vix_panic"]:
            regime = "skip"
            notes.append(f"VIX={vix:.1f} above panic threshold; skip the week")
        elif vix >= cfg["macro"]["vix_high"]:
            regime = "defensive"
            notes.append(f"VIX={vix:.1f} elevated; defensive thresholds")

    if ten_year is not None and three_month is not None:
        spread = ten_year - three_month
        if spread < -1.0:
            notes.append(f"Yield curve deeply inverted ({spread:+.2f}%)")
            if regime == "normal":
                regime = "defensive"

    if spx_above is False:
        notes.append("S&P 500 below 200-day SMA — broad downtrend")
        if regime == "normal":
            regime = "defensive"

    snap = MacroSnapshot(
        vix=vix,
        ten_year=ten_year,
        three_month=three_month,
        spx_above_200dma=spx_above,
        regime=regime,
        notes=notes,
    )
    log.info("macro: %s", snap)
    return snap


def _last_close(ticker: str) -> float | None:
    try:
        df = yf.Ticker(ticker).history(period="5d", auto_adjust=False)
        if df.empty:
            return None
        return float(df["Close"].dropna().iloc[-1])
    except Exception as exc:
        log.warning("macro fetch failed %s: %s", ticker, exc)
        return None


def _spx_above_200dma() -> bool | None:
    try:
        df = yf.Ticker("^GSPC").history(period="1y", auto_adjust=True)
        if len(df) < 200:
            return None
        sma200 = df["Close"].rolling(200).mean().iloc[-1]
        return bool(df["Close"].iloc[-1] > sma200)
    except Exception:
        return None
