# Trade Ticket — Week 1

**Macro regime:** `normal`  
**Notes:** none flagged.

## 1. Close prior-week positions (Sell @ Market, MOO Monday)

_No open positions to close — first run._

## 2. Open new positions (MOO Monday)

| Symbol | Shares | Plan Px | Cost Basis | Limit Fallback | Rationale |
|---|---:|---:|---:|---:|---|
| CME | 0.6876 | $285.06 | $196.01 | $286.49 | RSI 21.1, pullback +5.6% vs 50d, vol×1.15, P/E 24.3 |
| CAH | 0.9807 | $199.85 | $195.99 | $200.85 | RSI 30.8, pullback +7.2% vs 50d, vol×1.00, P/E 28.8 |
| PCG | 11.8001 | $16.61 | $196.00 | $16.69 | RSI 33.7, pullback +7.2% vs 50d, vol×1.05, P/E 12.9 |
| GILD | 1.5031 | $130.40 | $196.00 | $131.05 | RSI 26.5, pullback +8.4% vs 50d, vol×0.93, P/E 19.2 |
| LHX | 0.6173 | $317.51 | $196.00 | $319.10 | RSI 19.1, pullback +10.3% vs 50d, vol×1.34, P/E 37.2 |

**Total deployed:** $980.00

## Manual entry in Fidelity

1. Trade > Stocks/ETFs.
2. For each row above:
   - Action: Buy   - Symbol: as listed   - Quantity: shares
   - Order Type: **MOO**, Time-in-Force: Day
   - If MOO is unavailable, use **LIMIT** at the listed Limit Fallback price.
3. Enter sells for the close-rows the same way at Market / Day.

## Basket import (Active Trader Pro)

Open Active Trader Pro > Trade > Baskets > Import. Select the `trade_ticket.csv`
in this folder and confirm orders.