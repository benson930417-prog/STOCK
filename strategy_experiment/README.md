# strategy_experiment

Local-only research artefacts. **Nothing in this folder touches the OCI
server pipeline or the daily cron.** Scripts and outputs here are personal
sandbox for evaluating trading-strategy ideas before deciding whether to
build them into the production app.

## What's here

| File | Purpose |
|---|---|
| `optimize_tactical_rules.py` | One-shot grid-search backtester. Sweeps FIRE/RETRIEVE percentiles, cooldown, lookback window, and baseline core %. Compares against DCA / Static rebalance / Buy & Hold. |
| `tactical_backtest_results.csv` | Output of the most recent run (gitignored — re-generated each time). |

## Running

From repo root:

```bash
# 5y backtest with 0050 as core, default (sweeps 30/40/50/60/70 core %)
python -m strategy_experiment.optimize_tactical_rules --years 5

# Try NASDAQ as core instead
python -m strategy_experiment.optimize_tactical_rules --core-ticker 00662 --years 5

# Quick 2y check using local DB (no yfinance fetch)
python -m strategy_experiment.optimize_tactical_rules --years 2
```

## Conclusion from runs so far

Across 1080-config grids and 2y/5y windows on either 0050 or 00662 as core:
**the tactical layer adds no Sharpe alpha** vs simple static rebalancing or
monthly DCA. Static 30/70 (defensive) consistently wins on Sharpe; Buy &
Hold wins on raw return.

The 市場脈動 tab decision (built / not built / built as behavioural aid)
depends on what you want: alpha (skip it) or systematic discipline against
emotional trading (build it with explicit "no edge" framing).
