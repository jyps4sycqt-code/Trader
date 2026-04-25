"""Headline sentiment.

Two outputs per ticker:
  1. has_hard_negative : True if any headline matches a kill keyword
  2. soft_score        : net positive-keyword count, normalized to [0,1]

We deliberately use a tiny keyword model rather than an LLM call — the
scanner runs offline on a Mac mini, no API budget, and the screener
already does the heavy lifting on price/volume.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from . import data as data_mod

log = logging.getLogger(__name__)


@dataclass
class NewsRead:
    ticker: str
    headline_count: int
    has_hard_negative: bool
    soft_score: float          # 0..1
    matched_negative: list[str]
    matched_positive: list[str]
    sample_headlines: list[str]


def read_news(ticker: str, cfg: dict) -> NewsRead:
    items = data_mod.fetch_news(ticker)
    cutoff = time.time() - cfg["news"]["lookback_days"] * 86400
    headlines = []
    for it in items:
        ts = it.get("providerPublishTime") or 0
        title = it.get("title") or ""
        if ts >= cutoff and title:
            headlines.append(title)

    neg_keys = [k.lower() for k in cfg["news"]["hard_negative_keywords"]]
    pos_keys = [k.lower() for k in cfg["news"]["soft_positive_keywords"]]
    matched_neg, matched_pos = [], []
    pos_hits = 0

    for h in headlines:
        hl = h.lower()
        for k in neg_keys:
            if k in hl:
                matched_neg.append(k)
        for k in pos_keys:
            if k in hl:
                matched_pos.append(k)
                pos_hits += 1

    soft = min(pos_hits / 3.0, 1.0)   # 3 positive hits saturates the score

    return NewsRead(
        ticker=ticker,
        headline_count=len(headlines),
        has_hard_negative=bool(matched_neg),
        soft_score=soft,
        matched_negative=sorted(set(matched_neg)),
        matched_positive=sorted(set(matched_pos)),
        sample_headlines=headlines[:5],
    )
