"""Trade ticket generation.

Two artifacts per Sunday:
  1. trade_ticket.csv  — Fidelity Active Trader Pro basket import format.
                         Columns: Account, Symbol, Action, Quantity,
                         OrderType, LimitPrice, TimeInForce.
  2. trade_ticket.md   — Human-readable manual-entry table for users
                         without basket access.

Sizing: split (budget * (1 - cash_buffer)) across N picks; round shares
to 4 decimals (Fidelity supports fractional). Cost basis = shares *
entry_price.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from pathlib import Path

from .portfolio import Position
from .screener import Candidate

log = logging.getLogger(__name__)


@dataclass
class Allocation:
    ticker: str
    shares: float
    entry_price: float        # Friday close used as planning anchor
    cost_basis: float
    limit_price: float        # for LIMIT fallback
    rationale: str


def size_basket(
    picks: list[Candidate],
    budget_usd: float,
    cash_buffer_pct: float,
    limit_buffer_pct: float,
) -> list[Allocation]:
    n = len(picks)
    if n == 0:
        return []
    deployable = budget_usd * (1.0 - cash_buffer_pct)
    per = deployable / n
    out: list[Allocation] = []
    for c in picks:
        shares = round(per / c.close, 4)
        cost = round(shares * c.close, 2)
        limit_px = round(c.close * (1.0 + limit_buffer_pct / 100.0), 2)
        out.append(Allocation(
            ticker=c.ticker,
            shares=shares,
            entry_price=c.close,
            cost_basis=cost,
            limit_price=limit_px,
            rationale=c.rationale,
        ))
    return out


def write_csv(
    closes: list[Position],
    opens: list[Allocation],
    out_path: str | Path,
    order_type: str,
    fallback_order_type: str,
) -> Path:
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "Account", "Symbol", "Action", "Quantity",
            "OrderType", "LimitPrice", "TimeInForce", "Notes",
        ])
        for pos in closes:
            w.writerow([
                "", pos.ticker, "Sell", round(pos.shares, 4),
                "Market", "", "Day", "Close prior week",
            ])
        for a in opens:
            w.writerow([
                "", a.ticker, "Buy", a.shares,
                order_type, "", "Day",
                f"Plan close ${a.entry_price:.2f}; fallback {fallback_order_type} ${a.limit_price:.2f}",
            ])
    log.info("wrote %s", p)
    return p


def write_markdown(
    closes: list[Position],
    opens: list[Allocation],
    macro_notes: list[str],
    regime: str,
    week_index: int,
    out_path: str | Path,
    order_type: str,
    fallback_order_type: str,
) -> Path:
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append(f"# Trade Ticket — Week {week_index}")
    lines.append("")
    lines.append(f"**Macro regime:** `{regime}`  ")
    if macro_notes:
        lines.append("**Notes:**")
        for n in macro_notes:
            lines.append(f"- {n}")
    else:
        lines.append("**Notes:** none flagged.")
    lines.append("")

    lines.append("## 1. Close prior-week positions (Sell @ Market, MOO Monday)")
    lines.append("")
    if not closes:
        lines.append("_No open positions to close — first run._")
    else:
        lines.append("| Symbol | Shares | Entry | Entry Date |")
        lines.append("|---|---:|---:|---|")
        for pos in closes:
            lines.append(
                f"| {pos.ticker} | {pos.shares:.4f} | ${pos.entry_price:.2f} | {pos.entry_date} |"
            )
    lines.append("")

    lines.append(f"## 2. Open new positions ({order_type} Monday)")
    lines.append("")
    lines.append("| Symbol | Shares | Plan Px | Cost Basis | Limit Fallback | Rationale |")
    lines.append("|---|---:|---:|---:|---:|---|")
    total = 0.0
    for a in opens:
        total += a.cost_basis
        lines.append(
            f"| {a.ticker} | {a.shares:.4f} | ${a.entry_price:.2f} | "
            f"${a.cost_basis:.2f} | ${a.limit_price:.2f} | {a.rationale} |"
        )
    lines.append("")
    lines.append(f"**Total deployed:** ${total:.2f}")
    lines.append("")
    symbols_csv = ",".join(a.ticker for a in opens)
    lines.append("## Watchlist (copy/paste)")
    lines.append("")
    lines.append("Paste this into Fidelity.com > News & Research > Watch List > "
                 "**Add Symbols** (or ATP > Watch List > Import) to get one-click "
                 "trade access to all five tickers — free, no Basket Trader needed.")
    lines.append("")
    lines.append(f"```\n{symbols_csv}\n```")
    lines.append("")
    lines.append("## Manual entry in Fidelity")
    lines.append("")
    lines.append("From the watchlist (or Trade > Stocks/ETFs), for each row in section 2:")
    lines.append("")
    lines.append("- **Action:** Buy")
    lines.append("- **Symbol:** as listed")
    lines.append("- **Quantity:** shares (Fidelity supports fractional)")
    lines.append(f"- **Order Type:** {order_type}; if unavailable, use {fallback_order_type} "
                 "at the Limit Fallback price")
    lines.append("- **Time-in-Force:** Day")
    if closes:
        lines.append("")
        lines.append("Enter sells for the section-1 rows the same way at Market / Day "
                     "(or skip if you already closed manually on Friday).")
    p.write_text("\n".join(lines))
    log.info("wrote %s", p)
    return p
