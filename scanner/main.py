"""Entry points.

  python -m scanner.main weekly  -> run the Sunday scan + ticket
  python -m scanner.main daily   -> run the daily monitor
  python -m scanner.main scan    -> dry-run scan, no state mutation
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

from . import data as data_mod
from . import news as news_mod
from . import portfolio
from . import screener
from . import ticket as ticket_mod
from .git_push import commit_and_push
from .macro import read_macro

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config.yaml"


def _load_cfg() -> dict:
    with CONFIG_PATH.open() as f:
        return yaml.safe_load(f)


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    # Third-party libraries are extremely chatty at DEBUG; mute them so
    # `-v` shows our scanner's debug output without drowning in HTTP traces.
    for noisy in ("yfinance", "peewee", "urllib3", "requests", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _today_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _scan(cfg: dict) -> tuple[list[screener.Candidate], object]:
    macro = read_macro(cfg)
    if macro.regime == "skip":
        logging.warning("macro regime is SKIP; returning empty picks")
        return [], macro

    tickers = data_mod.load_universe(cfg["universe"]["source"])
    history = data_mod.fetch_history(tickers, days=cfg["screener"]["min_history_days"])
    info = data_mod.fetch_info(list(history.keys()))

    candidates: list[screener.Candidate] = []
    reject_counter: dict[str, int] = {}
    for t, df in history.items():
        c = screener.build_candidate(t, df, info.get(t, {}), cfg, reject_counter)
        if c is not None:
            candidates.append(c)
    logging.info("built %d raw candidates", len(candidates))
    if reject_counter:
        logging.info("rejection breakdown: %s", dict(sorted(reject_counter.items())))

    # News only on the (much smaller) shortlist that passes hard filters,
    # to keep yfinance.news calls bounded.
    target_n = cfg["budget"]["num_positions"]
    pre_ranked, _ = screener.rank(candidates, cfg, macro, target_n=target_n)
    shortlist = pre_ranked[:25]
    for c in shortlist:
        c.news = news_mod.read_news(c.ticker, cfg)

    final, tier = screener.rank(shortlist, cfg, macro, target_n=target_n)
    logging.info("final ranking via tier=%s, %d survivors", tier, len(final))
    return final, macro


def cmd_weekly(cfg: dict) -> int:
    state = portfolio.load_state(REPO_ROOT / cfg["execution"]["state_file"])
    today = _today_iso()

    final, macro = _scan(cfg)
    picks = screener.diversify(final, cfg["budget"]["num_positions"])

    if macro.regime == "skip":
        # Still close prior week per the user's rule.
        quotes = {p.ticker: data_mod.fetch_quote(p.ticker) or p.entry_price for p in state.open}
        portfolio.close_all_positions(state, quotes, today)
        portfolio.save_state(state, REPO_ROOT / cfg["execution"]["state_file"])
        commit_and_push(REPO_ROOT, cfg, f"weekly: panic regime, no opens ({today})")
        return 0

    if not picks:
        logging.error("no qualifying picks; aborting weekly run")
        return 2

    # 1. Close prior positions (use Friday-close quote as exit reference).
    quotes = {p.ticker: data_mod.fetch_quote(p.ticker) or p.entry_price for p in state.open}
    closed_record = portfolio.close_all_positions(state, quotes, today)

    # 2. Size new basket.
    allocations = ticket_mod.size_basket(
        picks=picks,
        budget_usd=cfg["budget"]["weekly_usd"],
        cash_buffer_pct=cfg["budget"]["cash_buffer_pct"],
        limit_buffer_pct=cfg["execution"]["limit_buffer_pct"],
    )

    # 3. Write artifacts.
    out_dir = REPO_ROOT / cfg["execution"]["reports_dir"] / today
    closes_for_csv = [
        portfolio.Position(
            ticker=row["ticker"],
            shares=row["shares"],
            entry_price=row["entry_price"],
            entry_date=row["entry_date"],
            cost_basis=round(row["entry_price"] * row["shares"], 2),
        )
        for row in closed_record["positions"]
    ] if closed_record["positions"] else []

    ticket_mod.write_csv(
        closes_for_csv,
        allocations,
        out_dir / "trade_ticket.csv",
        order_type=cfg["execution"]["order_type"],
        fallback_order_type=cfg["execution"]["fallback_order_type"],
    )
    ticket_mod.write_markdown(
        closes_for_csv,
        allocations,
        macro_notes=macro.notes,
        regime=macro.regime,
        week_index=state.week_index + 1,
        out_path=out_dir / "trade_ticket.md",
        order_type=cfg["execution"]["order_type"],
        fallback_order_type=cfg["execution"]["fallback_order_type"],
    )
    _write_scan_report(out_dir / "scan_report.md", final, picks, macro, state)

    # 4. Update state with the new opens (planning entry = Friday close).
    portfolio.open_positions(
        state,
        [
            {
                "ticker": a.ticker,
                "shares": a.shares,
                "entry_price": a.entry_price,
                "cost_basis": a.cost_basis,
                "rationale": a.rationale,
            }
            for a in allocations
        ],
        entry_date=today,
    )
    portfolio.save_state(state, REPO_ROOT / cfg["execution"]["state_file"])

    commit_and_push(
        REPO_ROOT,
        cfg,
        f"weekly: trade ticket {today} ({', '.join(a.ticker for a in allocations)})",
    )
    return 0


def cmd_daily(cfg: dict) -> int:
    state = portfolio.load_state(REPO_ROOT / cfg["execution"]["state_file"])
    today = _today_iso()
    out_path = REPO_ROOT / cfg["execution"]["reports_dir"] / today / "monitor.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    lines = [f"# Daily Monitor — {today}", ""]
    if not state.open:
        lines.append("_No open positions._")
    else:
        lines.append("| Symbol | Shares | Entry | Last | P&L $ | P&L % |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        total = 0.0
        for pos in state.open:
            last = data_mod.fetch_quote(pos.ticker)
            if last is None:
                last = pos.entry_price
            pnl_usd = (last - pos.entry_price) * pos.shares
            pnl_pct = (last / pos.entry_price - 1.0) * 100.0
            total += pnl_usd
            lines.append(
                f"| {pos.ticker} | {pos.shares:.4f} | ${pos.entry_price:.2f} | "
                f"${last:.2f} | ${pnl_usd:+.2f} | {pnl_pct:+.2f}% |"
            )
        lines.append("")
        lines.append(f"**Basket P&L:** ${total:+.2f}")
    out_path.write_text("\n".join(lines))

    commit_and_push(REPO_ROOT, cfg, f"daily monitor {today}")
    return 0


def cmd_scan(cfg: dict) -> int:
    """Dry-run: scan and print the top picks without touching state."""
    final, macro = _scan(cfg)
    picks = screener.diversify(final, cfg["budget"]["num_positions"])
    print(f"Macro regime: {macro.regime}")
    for n in macro.notes:
        print(f"  - {n}")
    print(f"\nTop {len(picks)} picks:")
    for c in picks:
        print(f"  {c.ticker:<6}  score={c.score:.3f}  {c.rationale}")
    return 0


def _write_scan_report(
    path: Path,
    all_ranked: list[screener.Candidate],
    picks: list[screener.Candidate],
    macro,
    state: portfolio.State,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# Scan Report — {_today_iso()}", ""]
    lines.append(f"Macro regime: **{macro.regime}**  VIX={macro.vix}  10y={macro.ten_year}  3m={macro.three_month}")
    if macro.notes:
        for n in macro.notes:
            lines.append(f"- {n}")
    lines.append("")
    lines.append(f"## Picks (week {state.week_index + 1})")
    lines.append("")
    lines.append("| Rank | Symbol | Sector | Score | RSI | Pullback% | Vol× | P/E |")
    lines.append("|---:|---|---|---:|---:|---:|---:|---:|")
    for i, c in enumerate(picks, 1):
        pe = f"{c.pe_ratio:.1f}" if c.pe_ratio else "—"
        lines.append(
            f"| {i} | {c.ticker} | {c.sector} | {c.score:.3f} | "
            f"{c.rsi:.1f} | {c.pullback_pct:+.1f}% | {c.vol_ratio:.2f} | {pe} |"
        )
    lines.append("")
    lines.append("## Picks — detail")
    for c in picks:
        lines.append(f"### {c.ticker}")
        lines.append(f"- Sector: {c.sector}")
        lines.append(f"- Score components: " + ", ".join(
            f"{k}={v:.2f}" for k, v in c.score_components.items()
        ))
        if c.news:
            lines.append(f"- Headlines (last 7d): {c.news.headline_count}")
            if c.news.matched_positive:
                lines.append(f"- Positive keywords: {c.news.matched_positive}")
            for h in c.news.sample_headlines:
                lines.append(f"  - {h}")
        lines.append("")
    lines.append("## Runners-up (next 10)")
    runners = [c for c in all_ranked if c not in picks][:10]
    for c in runners:
        lines.append(f"- {c.ticker} (score {c.score:.3f}) — {c.rationale}")
    path.write_text("\n".join(lines))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["weekly", "daily", "scan"])
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    cfg = _load_cfg()
    if args.command == "weekly":
        return cmd_weekly(cfg)
    if args.command == "daily":
        return cmd_daily(cfg)
    return cmd_scan(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
