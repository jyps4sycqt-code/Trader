"""Persistent position state.

state/positions.json shape:
{
  "as_of": "2026-04-19T18:00:00Z",
  "week_index": 42,
  "open": [
    {"ticker": "AAPL", "shares": 1.234, "entry_price": 162.0,
     "entry_date": "2026-04-21", "cost_basis": 200.00, "rationale": "..."}
  ],
  "history": [
    {"week_index": 41, "opened": "2026-04-14", "closed": "2026-04-21",
     "positions": [{"ticker":"AAPL", "shares":..., "entry":..., "exit":...,
                    "pnl_usd":..., "pnl_pct":...}]}
  ]
}
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class Position:
    ticker: str
    shares: float
    entry_price: float
    entry_date: str
    cost_basis: float
    rationale: str = ""


@dataclass
class State:
    week_index: int = 0
    open: list[Position] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)
    as_of: str = ""


def _isoformat() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_state(path: str | Path) -> State:
    p = Path(path)
    if not p.exists():
        return State(as_of=_isoformat())
    raw = json.loads(p.read_text())
    return State(
        week_index=raw.get("week_index", 0),
        open=[Position(**pos) for pos in raw.get("open", [])],
        history=raw.get("history", []),
        as_of=raw.get("as_of", _isoformat()),
    )


def save_state(state: State, path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    state.as_of = _isoformat()
    payload: dict[str, Any] = {
        "as_of": state.as_of,
        "week_index": state.week_index,
        "open": [asdict(pos) for pos in state.open],
        "history": state.history,
    }
    p.write_text(json.dumps(payload, indent=2))


def close_all_positions(
    state: State,
    quotes: dict[str, float],
    close_date: str,
) -> dict:
    """Mark current open positions closed using `quotes`. Returns the
    history record we appended (for reporting)."""
    closed_rows = []
    for pos in state.open:
        exit_price = quotes.get(pos.ticker)
        if exit_price is None:
            log.warning("no exit quote for %s; skipping close pricing", pos.ticker)
            exit_price = pos.entry_price  # neutral assumption
        pnl_usd = (exit_price - pos.entry_price) * pos.shares
        pnl_pct = (exit_price / pos.entry_price - 1.0) * 100.0
        closed_rows.append({
            "ticker": pos.ticker,
            "shares": pos.shares,
            "entry_price": pos.entry_price,
            "exit_price": exit_price,
            "entry_date": pos.entry_date,
            "exit_date": close_date,
            "pnl_usd": round(pnl_usd, 2),
            "pnl_pct": round(pnl_pct, 2),
        })

    record = {
        "week_index": state.week_index,
        "opened": state.open[0].entry_date if state.open else None,
        "closed": close_date,
        "positions": closed_rows,
        "total_pnl_usd": round(sum(r["pnl_usd"] for r in closed_rows), 2),
    }
    if state.open:
        state.history.append(record)
    state.open = []
    return record


def open_positions(
    state: State,
    allocations: list[dict],
    entry_date: str,
) -> None:
    """allocations: [{ticker, shares, entry_price, cost_basis, rationale}, ...]"""
    state.week_index += 1
    state.open = [
        Position(
            ticker=a["ticker"],
            shares=a["shares"],
            entry_price=a["entry_price"],
            entry_date=entry_date,
            cost_basis=a["cost_basis"],
            rationale=a.get("rationale", ""),
        )
        for a in allocations
    ]
