"""Ranking pipeline.

For each ticker with adequate history we compute:
  * RSI(14)
  * 50-day and 200-day SMA
  * pullback%   = (sma50 - close) / sma50 * 100
  * vol_ratio   = mean(volume[-5:]) / mean(volume[-30:])
  * fundamental sanity from yfinance .info
We then apply hard filters (universe quality + macro regime) and a
weighted composite score for the survivors. Top-N by score = picks.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .macro import MacroSnapshot
from .news import NewsRead

log = logging.getLogger(__name__)


@dataclass
class Candidate:
    ticker: str
    close: float
    sma50: float
    sma200: float
    rsi: float
    pullback_pct: float
    vol_ratio: float
    market_cap: float
    sector: str
    pe_ratio: float | None
    earnings_positive: bool
    score: float = 0.0
    score_components: dict = field(default_factory=dict)
    news: NewsRead | None = None
    rationale: str = ""


def _rsi(close: pd.Series, period: int) -> float | None:
    if len(close) < period + 1:
        return None
    delta = close.diff().dropna()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    if loss.iloc[-1] == 0 or math.isnan(loss.iloc[-1]):
        return 100.0 if gain.iloc[-1] > 0 else 50.0
    rs = gain.iloc[-1] / loss.iloc[-1]
    return float(100 - 100 / (1 + rs))


def build_candidate(
    ticker: str,
    history: pd.DataFrame,
    info: dict,
    cfg: dict,
) -> Candidate | None:
    if "Close" not in history or len(history) < cfg["screener"]["min_history_days"]:
        return None

    close = history["Close"].dropna()
    volume = history["Volume"].dropna()
    if close.empty or volume.empty:
        return None

    last = float(close.iloc[-1])
    sma50 = float(close.rolling(cfg["screener"]["sma_short"]).mean().iloc[-1])
    sma200 = float(close.rolling(cfg["screener"]["sma_long"]).mean().iloc[-1])
    rsi = _rsi(close, cfg["screener"]["rsi_period"])
    if rsi is None or math.isnan(sma50) or math.isnan(sma200):
        return None

    pullback = (sma50 - last) / sma50 * 100.0
    vol_short = volume.tail(5).mean()
    vol_long = volume.tail(30).mean()
    vol_ratio = float(vol_short / vol_long) if vol_long else 0.0

    avg_dollar_vol = float((close * volume).tail(30).mean())
    mkt_cap = float(info.get("marketCap") or 0)
    sector = str(info.get("sector") or "Unknown")
    pe = info.get("trailingPE")
    pe_val = float(pe) if isinstance(pe, (int, float)) and not math.isnan(float(pe)) else None
    eps_ttm = info.get("trailingEps")
    earnings_positive = bool(eps_ttm and eps_ttm > 0)

    # Liquidity filter
    if avg_dollar_vol < cfg["universe"]["min_avg_dollar_volume"]:
        return None
    if mkt_cap < cfg["universe"]["min_market_cap_usd"]:
        return None
    if sector in cfg["universe"]["exclude_sectors"]:
        return None
    if ticker in cfg["universe"]["exclude_tickers"]:
        return None

    return Candidate(
        ticker=ticker,
        close=last,
        sma50=sma50,
        sma200=sma200,
        rsi=rsi,
        pullback_pct=pullback,
        vol_ratio=vol_ratio,
        market_cap=mkt_cap,
        sector=sector,
        pe_ratio=pe_val,
        earnings_positive=earnings_positive,
    )


def _passes_hard_filters(c: Candidate, cfg: dict, regime: str) -> tuple[bool, str]:
    rsi_max = cfg["screener"]["rsi_max"]
    pullback_max = cfg["screener"]["pullback_pct_max"]
    if regime == "defensive":
        rsi_max = min(rsi_max, cfg["macro"]["defensive_rsi_max"])
        pullback_max = min(pullback_max, cfg["macro"]["defensive_pullback_max"])

    if c.rsi > rsi_max:
        return False, f"RSI {c.rsi:.1f} > {rsi_max}"
    if c.pullback_pct < cfg["screener"]["pullback_pct_min"]:
        return False, f"pullback {c.pullback_pct:.1f}% < min"
    if c.pullback_pct > pullback_max:
        return False, f"pullback {c.pullback_pct:.1f}% > max"
    if cfg["screener"]["require_above_sma_long"] and c.close < c.sma200:
        return False, "below 200-day SMA"
    if c.vol_ratio < cfg["screener"]["volume_spike_min"]:
        return False, f"volume ratio {c.vol_ratio:.2f} < min"
    if not c.earnings_positive:
        return False, "no positive trailing EPS"
    return True, ""


def _score(c: Candidate, cfg: dict) -> tuple[float, dict]:
    rsi_max = cfg["screener"]["rsi_max"]
    oversold = max(0.0, (rsi_max - c.rsi) / rsi_max)

    pmin = cfg["screener"]["pullback_pct_min"]
    pmax = cfg["screener"]["pullback_pct_max"]
    sweet = (pmin + pmax) / 2.0
    width = (pmax - pmin) / 2.0
    dip_quality = max(0.0, 1.0 - abs(c.pullback_pct - sweet) / width)

    vol_signal = min(1.0, max(0.0, (c.vol_ratio - 1.0) / 1.5))

    fund = 0.5
    if c.earnings_positive:
        fund += 0.25
    if c.pe_ratio and 5 <= c.pe_ratio <= 35:
        fund += 0.25
    fund = min(1.0, fund)

    sentiment = c.news.soft_score if c.news else 0.0

    w = cfg["ranking"]["weights"]
    components = {
        "oversold": oversold,
        "dip_quality": dip_quality,
        "volume_signal": vol_signal,
        "fundamentals": fund,
        "sentiment": sentiment,
    }
    score = sum(components[k] * w[k] for k in components)
    return float(score), components


def rank(
    candidates: list[Candidate],
    cfg: dict,
    macro: MacroSnapshot,
) -> list[Candidate]:
    survivors: list[Candidate] = []
    for c in candidates:
        ok, reason = _passes_hard_filters(c, cfg, macro.regime)
        if not ok:
            log.debug("drop %s: %s", c.ticker, reason)
            continue
        if c.news and c.news.has_hard_negative:
            log.info("drop %s: hard-negative news %s", c.ticker, c.news.matched_negative)
            continue
        c.score, c.score_components = _score(c, cfg)
        c.rationale = (
            f"RSI {c.rsi:.1f}, pullback {c.pullback_pct:+.1f}% vs 50d, "
            f"vol×{c.vol_ratio:.2f}, P/E "
            f"{c.pe_ratio:.1f}" if c.pe_ratio else
            f"RSI {c.rsi:.1f}, pullback {c.pullback_pct:+.1f}% vs 50d, vol×{c.vol_ratio:.2f}"
        )
        survivors.append(c)

    survivors.sort(key=lambda x: x.score, reverse=True)
    return survivors


def diversify(picks: list[Candidate], n: int) -> list[Candidate]:
    """Take top-N with at most 2 per sector."""
    out: list[Candidate] = []
    sector_count: dict[str, int] = {}
    for c in picks:
        if sector_count.get(c.sector, 0) >= 2:
            continue
        out.append(c)
        sector_count[c.sector] = sector_count.get(c.sector, 0) + 1
        if len(out) >= n:
            break
    # If sector limit starved us, backfill from remaining picks
    if len(out) < n:
        chosen = {c.ticker for c in out}
        for c in picks:
            if c.ticker in chosen:
                continue
            out.append(c)
            if len(out) >= n:
                break
    return out
