# Weekly Stock Scanner

A Mac mini side project that picks 5 "underbought, mean-revert up over a
week" stocks every Sunday evening, generates a Fidelity-ready trade
ticket, and pushes the results to GitHub. Closes the prior week's basket
on the same Sunday run.

> **Reality check.** The picker is a simple heuristic — RSI + pullback +
> volume + sector cap, with a news kill-list and a macro overlay. It is
> *not* alpha. Treat it as entertainment with money you can lose, exactly
> as you described it.

## What it does

| When (local Mac mini time) | Job | Output |
|---|---|---|
| Sun 18:00 (pre-market Mon) | `weekly` | trade_ticket.csv + trade_ticket.md + scan_report.md, push to GitHub |
| Mon–Fri 17:00 | `daily` | monitor.md (P&L of open basket), push to GitHub |
| Fri after manual sell | `close-week` (manual) | mutates state to mark basket closed, pushes to GitHub |

## Workflow

1. **Friday during market hours** — manually sell the 5 positions in Fidelity.
2. **Friday after the sell** — run `python -m scanner.main close-week` on the
   Mac mini. Exit prices default to the latest available quote (≈ Friday close);
   pass `--fills 'CME=287.10,CAH=201.50,...'` if you want fill-accurate history.
3. **Sunday 18:00** — the weekly launchd job runs automatically. Because state
   shows an empty `open[]`, it produces a **buy-only** ticket for Monday.
4. **Monday at the open** — place the buys via Fidelity basket import or
   manual entry from `trade_ticket.md`.

## Setup on the Mac mini

```bash
cd ~/Trader
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Optional smoke test (no state mutation)
python -m scanner.main scan -v
```

### Schedule with launchd

```bash
mkdir -p ~/Library/LaunchAgents
cp launchd/com.trader.weekly.plist ~/Library/LaunchAgents/
cp launchd/com.trader.daily.plist  ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.trader.weekly.plist
launchctl load ~/Library/LaunchAgents/com.trader.daily.plist
```

Edit `WorkingDirectory` and `EnvironmentVariables.PATH` in the plists if
your repo lives somewhere other than `~/Trader` or your venv is elsewhere.

### GitHub credentials

`launchd` runs unattended, so the push needs a credential helper that
doesn't prompt:

```bash
git config --global credential.helper osxkeychain
# Then push once interactively to seed the keychain:
git push -u origin claude/weekly-stock-scanner-fZTSa
```

Alternatively, configure an SSH remote and have the SSH agent loaded at
login.

## Using the trade ticket

Each Sunday run drops `reports/YYYY-MM-DD/`:

- `trade_ticket.csv` — Fidelity Active Trader Pro **basket import**
  format (Trade > Baskets > Import).
- `trade_ticket.md` — manual-entry table for entering each trade by hand
  if you don't have basket access. The "Plan Px" is Friday's close; if
  Market-on-Open isn't offered, fall back to a LIMIT at the listed price
  (Friday close + 0.5%).
- `scan_report.md` — rationale per pick, runners-up, macro context.

The scanner uses **fractional shares** to evenly split the budget. If you
prefer whole shares, round down in Fidelity at entry — the cost basis
will be a bit lower than the planned $200/position.

## How the picks are made

1. Pull S&P 500 + Nasdaq 100 (~540 names, deduped).
2. Drop names below $2B market cap or $25M average daily dollar volume.
3. Hard filters:
   - RSI(14) ≤ 35 (≤ 30 in defensive macro regime)
   - 2–12% below 50-day SMA (≤ 8% in defensive)
   - Above 200-day SMA (uptrend intact)
   - 5-day volume ≥ 1.3× 30-day (capitulation/interest signal)
   - Positive trailing EPS
   - No "fraud / investigation / bankruptcy / SEC charges / FDA rejects"
     headlines in the last 7 days.
4. Composite score = 0.30 oversold + 0.25 dip-quality + 0.15 volume +
   0.15 fundamentals + 0.15 news sentiment.
5. Top-ranked five, capped at 2 per sector for diversification.

Macro regime (VIX, yield curve, S&P 200dma) tightens thresholds when the
broad market is risk-off; if VIX ≥ 35 the scanner closes prior positions
and skips opening new ones for the week.

## State

`state/positions.json` is the source of truth for the currently open
basket. It survives across runs and is committed alongside the reports
so the scanner is recoverable from a fresh checkout. **Don't hand-edit
it during a live week** — close the week first via a `weekly` run.

## Files

```
config.yaml                # all tunables
requirements.txt
scanner/
  data.py                  # universe + history + quotes via yfinance
  macro.py                 # VIX, treasuries, SPX trend
  news.py                  # keyword sentiment on yfinance headlines
  screener.py              # candidate building + ranking + diversify
  portfolio.py             # persistent positions.json
  ticket.py                # Fidelity CSV + markdown
  git_push.py              # auto-commit + push
  main.py                  # weekly | daily | scan
scripts/
  run_weekly.sh
  run_daily.sh
launchd/
  com.trader.weekly.plist
  com.trader.daily.plist
state/
  positions.json
reports/
  YYYY-MM-DD/
    trade_ticket.csv
    trade_ticket.md
    scan_report.md
    monitor.md
```
