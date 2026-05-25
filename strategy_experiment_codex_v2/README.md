# Strategy Experiment Codex V2

Handoff package for the ETF strategy experiments built during the Codex review.
The goal was not to make a trading bot. The goal was to answer two practical
allocation questions with reproducible code:

1. For one ETF at a time, does tactical cash deployment beat simple buy and
   hold or DCA?
2. When Taiwan stocks look hot, what is the least-regrettable way to deploy a
   remaining 40% cash bullet over the next three months?

The experiment intentionally does not convert currencies. Each ETF is tested in
its native price series.

## Folder Map

Tracked handoff files:

- `run_v2.py`  
  Original pure-Python strategy-grid runner. It is slower, but easier to debug
  and keeps the reference implementation of the strategy definitions.
- `run_v2_fast.py`  
  Numba implementation of the same broad grid. This is the recommended runner.
  It evaluates 78,341 strategy specs per ETF/window in seconds after compile.
- `overheat_bullet_experiment.py`  
  Separate event-study style experiment. It finds historical TAIEX hot/warm
  dates, then tests how to deploy a 40% cash bullet into `0050.TW` over the
  following 63 trading days.
- `generate_zh_tw_summary_pdf.py`  
  Regenerates the Traditional Chinese PDF through Chromium/HTML. This exists
  because ReportLab's direct CJK PDF path rendered Chinese as question marks.
- `summary_v2.csv` and `top_v2.txt`  
  Compact results from the latest full sweep over `0050`, `SPY`, and `QQQ`
  across 5y and 10y windows.
- `overheat_bullet_summary_0050_TW.csv`,
  `overheat_events_0050_TW.csv`,
  `overheat_bullet_results_0050_TW.csv`,
  `overheat_bullet_takeaways_0050_TW.txt`  
  Current 0050 overheat/bullet experiment outputs.
- `strategy_experiment_codex_v2_trader_summary.pdf` and
  `strategy_experiment_codex_v2_trader_summary_zh_tw.pdf`  
  Beginner-facing summaries of the experiment and its caveats.

Ignored local files:

- `.yfinance_cache/`
- `__pycache__/`
- `results_v2_all.csv` because it is very large and can be regenerated.
- `*_preview.png` from PDF visual checks.

## Main Strategy Grid

Strategy families:

- `all_in`: buy fully on day 1.
- `dca_5pct_monthly`: deploy 5% per month until fully invested.
- `static`: fixed ETF/cash allocation with monthly rebalance.
- `tactical`: keep an optional core allocation and deploy cash bullets when
  weakness signals fire. Optional retrieve rules can pull bullets back to cash
  when the ETF looks extended.

The tactical engine is stateful. Bullets stay deployed until a retrieve rule
acts, so this is not a daily signal table.

Run the recommended fast engine from repo root:

```bash
python -m strategy_experiment_codex_v2.run_v2_fast --tickers 0050 SPY QQQ --years 5 10
```

Run one ETF:

```bash
python -m strategy_experiment_codex_v2.run_v2_fast --tickers 0050 --years 5 10
```

The first Numba run has compile overhead. Later windows are much faster.

Debug/reference runner:

```bash
python -m strategy_experiment_codex_v2.run_v2 --tickers 0050 --years 5 10 --workers 0 --chunk-size 1000 --progress-every 1000
```

Important note: the fast runner intentionally fixes two metric issues found
during review:

- CAGR uses actual calendar time span, not `rows / 252`.
- Static strategies initialize at each train/holdout segment start.

The old pure-Python runner is still useful for debugging strategy logic, but
the fast runner is the one to use for current results.

## Overheat Bullet Experiment

This experiment answers the user's current portfolio question:

> If I am already 60% invested and have 40% cash, and Taiwan is hot, should I
> deploy now, wait for a dip, deploy by time, or mix both?

The script:

1. Downloads/caches `^TWII` and the target ETF, default `0050.TW`.
2. Computes TAIEX hot/warm event dates using trailing metrics:
   - distance from 200-day moving average z-score,
   - 30-day return,
   - 60-day return,
   - distance from 1-year high.
3. Keeps events separated by cooldown windows so one rally does not create too
   many duplicate samples.
4. Simulates 63 trading days forward for multiple 40% deployment rules.

Run:

```bash
python strategy_experiment_codex_v2/overheat_bullet_experiment.py --asset 0050.TW --start 2003-01-01
```

Current event groups:

- `similar_now_strict`: very close to the current extreme setup. Few samples.
- `hot_near_high`: stricter hot setup near highs.
- `stretch_only`: strong MA200 stretch with weaker momentum requirements.
- `warm_near_high`: broader warm setup, at least 10 samples.
- `momentum_near_high`: broader momentum setup, at least 10 samples.
- `near_high_positive_momentum`: broad hot/warm near-high setup with 25 samples.

Current 0050 result pattern:

- Waiting with all 40% in cash usually has the lowest 3-month return.
- Immediate deployment usually has the highest median return but also larger
  drawdown.
- Mixed time/dip deployment is the more behaviorally robust compromise.

Practical reading: avoid both extremes. Do not leave all 40% waiting for a
perfect dip, but do not force all 40% into a hot tape at once either.

## Result Interpretation

The key lesson from the main strategy grid:

- Buy and hold remains the benchmark.
- DCA often makes the path easier but rarely maximizes return.
- Tactical rules can improve Sharpe and drawdown, but often give up CAGR.
- A tactical rule is only interesting if it survives holdout and has a simple,
  explainable market story.

The key lesson from the overheat bullet study:

- In hot markets, completely waiting for a dip is often a regret engine.
- Fully chasing immediately can work, but the drawdown cost is real.
- A predefined hybrid rule is better than emotional all-in/all-out switching.

## Known Limitations

- This is historical research, not a forecast.
- Yahoo data quality can change.
- Currency conversion is intentionally excluded.
- Taxes, spreads, tracking error, and Taiwan ETF premium/discount are not fully
  modeled.
- Testing many specs creates overfitting risk. Prefer robust families over the
  single prettiest result.
- The overheat event study uses 0050 as the long-history Taiwan beta proxy.
  Shorter-history ETFs such as 00981A or 00878 can be tested, but sample quality
  will be weaker.

## Suggested Next Work

- Add a report generator that turns `summary_v2.csv` and overheat outputs into
  a single markdown/PDF handoff.
- Add transaction-cost sensitivity checks.
- Add Taiwan ETF premium/discount or NAV slippage assumptions for listed US ETF
  wrappers.
- Re-run overheat deployment against 00981A/00878 once their histories are long
  enough to produce meaningful samples.
