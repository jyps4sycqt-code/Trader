"""Market data fetch.

Universe: union of S&P 500 + Nasdaq 100 tickers, scraped from Wikipedia
once and cached on disk. Per-ticker history pulled in batched yfinance
downloads. Failures are tolerated — we drop tickers that don't return
clean data rather than aborting the scan.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests
import yfinance as yf

_WIKI_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0 Safari/537.36"
)

log = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache"
CACHE_DIR.mkdir(exist_ok=True)
UNIVERSE_CACHE = CACHE_DIR / "universe.json"
UNIVERSE_TTL_SECONDS = 7 * 24 * 3600


@dataclass
class TickerBundle:
    ticker: str
    history: pd.DataFrame      # OHLCV indexed by date
    info: dict                  # yfinance .info snapshot
    news: list[dict]            # yfinance .news list


def load_universe(source: str) -> list[str]:
    if source != "sp500_nasdaq100":
        raise ValueError(f"unknown universe source: {source}")

    if UNIVERSE_CACHE.exists():
        age = time.time() - UNIVERSE_CACHE.stat().st_mtime
        if age < UNIVERSE_TTL_SECONDS:
            return json.loads(UNIVERSE_CACHE.read_text())

    sp500 = _read_wiki_table(
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        symbol_col="Symbol",
    )
    ndx = _read_wiki_table(
        "https://en.wikipedia.org/wiki/Nasdaq-100",
        symbol_col="Ticker",
    )
    tickers = sorted({_normalize(t) for t in (sp500 + ndx) if t})
    UNIVERSE_CACHE.write_text(json.dumps(tickers))
    log.info("universe cached: %d tickers", len(tickers))
    return tickers


def _read_wiki_table(url: str, symbol_col: str) -> list[str]:
    # Wikipedia returns 403 to pandas' default urllib UA; fetch with a
    # browser-like UA first, then hand the HTML body to pandas.
    resp = requests.get(url, headers={"User-Agent": _WIKI_USER_AGENT}, timeout=30)
    resp.raise_for_status()
    tables = pd.read_html(resp.text)
    for tbl in tables:
        if symbol_col in tbl.columns:
            return tbl[symbol_col].astype(str).tolist()
    raise RuntimeError(f"no table with column {symbol_col} at {url}")


def _normalize(t: str) -> str:
    # yfinance uses '-' instead of '.' for share classes (BRK.B -> BRK-B).
    return t.strip().upper().replace(".", "-")


def fetch_history(
    tickers: Iterable[str],
    days: int,
    batch_size: int = 50,
) -> dict[str, pd.DataFrame]:
    tickers = list(tickers)
    end = datetime.utcnow()
    start = end - timedelta(days=days + 30)
    out: dict[str, pd.DataFrame] = {}

    for i in range(0, len(tickers), batch_size):
        batch = tickers[i : i + batch_size]
        try:
            df = yf.download(
                tickers=" ".join(batch),
                start=start,
                end=end,
                auto_adjust=True,
                progress=False,
                threads=True,
                group_by="ticker",
            )
        except Exception as exc:
            log.warning("batch download failed (%s..%s): %s", batch[0], batch[-1], exc)
            continue

        for t in batch:
            try:
                sub = df[t] if (t,) in df.columns or t in df.columns.get_level_values(0) else None
                if sub is None or sub.dropna().empty:
                    continue
                sub = sub.dropna(subset=["Close"]).copy()
                if len(sub) < 60:
                    continue
                out[t] = sub
            except Exception:
                continue

    log.info("history fetched for %d/%d tickers", len(out), len(tickers))
    return out


def fetch_info(tickers: Iterable[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for t in tickers:
        try:
            info = yf.Ticker(t).get_info()
            if info:
                out[t] = info
        except Exception as exc:
            log.debug("info fetch failed %s: %s", t, exc)
    return out


def fetch_news(ticker: str) -> list[dict]:
    try:
        return yf.Ticker(ticker).news or []
    except Exception:
        return []


def fetch_quote(ticker: str) -> float | None:
    """Latest available close for execution sizing."""
    try:
        df = yf.Ticker(ticker).history(period="5d", auto_adjust=True)
        if df.empty:
            return None
        return float(df["Close"].iloc[-1])
    except Exception:
        return None
