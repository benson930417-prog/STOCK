# Strategy Experiment V2

Separate single-ETF tactical deployment tests for `0050`, `SPY`, and `QQQ`.

This experiment asks one question at a time:

> For one ETF, is tactical cash deployment better than simply buying and
> holding the ETF?

No currency conversion is used. Each ETF is tested in its native price series.

## Strategy Families

- Baselines:
  - all-in from day 1
  - 5% monthly DCA until fully invested
  - static ETF/cash allocations with monthly rebalance
- Tactical:
  - permanent core allocation plus cash bullets
  - fire bullets on weakness
  - optionally retrieve bullets on overextension

The tactical engine is intentionally stateful: bullets remain deployed until a
retrieve rule pulls them back to cash.

## Run

From repo root:

Fast Numba engine, recommended:

```bash
python -m strategy_experiment_v2.run_v2_fast --tickers 0050 SPY QQQ --years 5 10
```

For one ETF:

```bash
python -m strategy_experiment_v2.run_v2_fast --tickers 0050 --years 5 10
```

The first run compiles the Numba engine, so the first ticker/window has a
one-time warm-up cost. Later evaluations are much faster.

Original pure-Python runner, kept for debugging:

```bash
python -m strategy_experiment_v2.run_v2 --tickers 0050 SPY QQQ --years 5 10 --workers 0 --chunk-size 1000 --progress-every 1000
```

For a faster first pass:

```bash
python -m strategy_experiment_v2.run_v2 --tickers 0050 --years 5 10 --workers 0 --chunk-size 1000 --progress-every 1000
```

The progress bar shows percent complete, elapsed time, and ETA for each
ticker/window pair.

Use `--workers 0` for all logical CPU cores, or `--workers 8` / `--workers 16`
to leave some machine headroom.

On Windows, larger `--chunk-size` values are usually faster because process
scheduling overhead is high. Good starting points are `500`, `1000`, or `2000`.

Outputs:

- `results_v2_all.csv`
- `summary_v2.csv`
- `top_v2.txt`

## Interpretation

A tactical strategy is only interesting if it beats the simple baselines on
holdout and does not require extreme turnover or fragile parameters. Full-sample
winners are treated as candidates, not proof.
