# strategy_experiment_codex

Independent audit copy of `strategy_experiment/`.

The original folder is left intact. This Codex version fixes implementation bugs
and re-runs the tactical idea under a train/holdout challenge.

## What changed

- Signals generated from a close execute on the next trading day.
- Starting buy commissions are included and measured against starting cash.
- Rebalance costs are charged on the actual traded buy/sell legs.
- Sharpe and Sortino use excess returns over a configurable risk-free rate.
- The grid is selected on an in-sample train segment and tested on later
  holdout data.
- Signal source is configurable: raw `^TWII` or the core ETF adjusted close.
- Midpoint reset is configurable because the original reset can truncate
  winning dip-buys.
- Cooldowns track the strongest signal seen during the pause instead of
  forgetting a deeper drawdown or stronger retrieve signal.

## Run

From repo root:

```bash
python -m strategy_experiment_codex.codex_tactical_audit --years 2 --core-ticker 0050 --signal-source core --no-midpoint-reset
```

Useful options:

- `--core-ticker 0050` or `--core-ticker 00662`
- `--bullet-ticker 00865B`
- `--signal-source taiex` or `--signal-source core`
- `--no-midpoint-reset`
- `--cooldown-mode track` or `--cooldown-mode rigid`

Each run writes:

- `codex_tactical_grid_train_{core}_{bullet}_{signal}_{reset}_{cooldown}.csv`
- `codex_audit_summary_{core}_{bullet}_{signal}_{reset}_{cooldown}.txt`

## Local DB Result

Window: `2024-05-24` to `2026-05-22`, split 60% train / 40% holdout.
Risk-free rate for excess Sharpe: `1.00%`.
Cooldown mode: `track`.

### 0050 core / 00865B bullet

| Signal | Midpoint reset | Holdout tactical return | Holdout tactical Sharpe | Best benchmark by Sharpe | Verdict |
| --- | --- | ---: | ---: | --- | --- |
| `taiex` | on | 21.05% | 2.52 | Static 30/70 weekly, Sharpe 5.49 | FAIL |
| `taiex` | off | 22.17% | 4.18 | Static 30/70 weekly, Sharpe 5.49 | FAIL |
| `core` | on | 25.42% | 2.93 | Static 30/70 weekly, Sharpe 5.49 | FAIL |
| `core` | off | 25.42% | 2.93 | Static 30/70 weekly, Sharpe 5.49 | FAIL |

`taiex + no reset` improves risk-adjusted holdout performance, but it still
does not beat the simple static defensive benchmark. Using core adjusted close
as the signal avoids the raw TAIEX dividend-season trap, but it also does not
rescue the strategy.

### 00662 core / 00865B bullet

| Signal | Midpoint reset | Holdout tactical return | Holdout tactical Sharpe | Best benchmark by Sharpe | Verdict |
| --- | --- | ---: | ---: | --- | --- |
| `taiex` | on | 14.43% | 2.06 | Static 30/70 weekly, Sharpe 3.54 | FAIL |
| `taiex` | off | 13.78% | 1.98 | Static 30/70 weekly, Sharpe 3.54 | FAIL |
| `core` | on | 19.45% | 2.04 | Static 30/70 weekly, Sharpe 3.54 | FAIL |
| `core` | off | 19.67% | 2.20 | Static 30/70 weekly, Sharpe 3.54 | FAIL |

The US-tech core variant also fails the holdout challenge. `core + no reset`
is the best tactical version here, but a simple weekly static blend remains
cleaner on Sharpe and drawdown.

## Long-Window Yahoo Result

Requested window: `10y`.
Actual aligned window: `2019-11-15` to `2026-05-22`, because `00865B` was only
listed in 2019. This is not a full 10-year core/bullet history, but it is the
longest available aligned window for the current default bullet ETF.

Split: 60% train / 40% holdout.
Train: `2019-11-15` to `2023-10-06`.
Holdout: `2023-10-11` to `2026-05-22`.
Price source: `yfinance`.
Risk-free rate for excess Sharpe: `1.00%`.
Cooldown mode: `track`.

### 0050 core / 00865B bullet

| Signal | Midpoint reset | Holdout tactical return | Holdout tactical Sharpe | Best benchmark by Sharpe | Verdict |
| --- | --- | ---: | ---: | --- | --- |
| `taiex` | on | 81.26% | 1.67 | Static 40/60 weekly, Sharpe 2.24 | FAIL |
| `taiex` | off | 61.56% | 1.37 | Static 40/60 weekly, Sharpe 2.24 | FAIL |
| `core` | on | 69.80% | 1.51 | Static 40/60 weekly, Sharpe 2.24 | FAIL |
| `core` | off | 58.17% | 1.30 | Static 40/60 weekly, Sharpe 2.24 | FAIL |

The best tactical variant by holdout Sharpe is `taiex + reset on`, but it still
falls well short of static `40/60`. Buy-and-hold has the highest raw return
(`227.83%`) but also larger drawdown (`-27.48%`) than static blends.

### 00662 core / 00865B bullet

Cleanest tested variant: `core` signal, midpoint reset off.

| Strategy | Holdout return | Holdout Sharpe | Max drawdown |
| --- | ---: | ---: | ---: |
| Chosen tactical | 37.68% | 0.78 | -17.78% |
| Buy & Hold 100% core | 90.78% | 1.24 | -24.19% |
| Static 40/60 weekly | 38.16% | 1.32 | -12.12% |

Again, tactical is not competitive on holdout Sharpe. It earns almost the same
return as static `40/60`, but with materially worse drawdown and lower Sharpe.

## Theory Cross-Check

- Sharpe is an excess-return measure, so the audit subtracts a risk-free daily
  return before annualizing the ratio. See William Sharpe's Stanford note:
  <https://web.stanford.edu/~wfsharpe/art/sr/SR.htm>
- Searching many signal combinations on one short sample invites data-mining
  bias. The train/holdout split is only a minimal defense, not full proof.
  See Novy-Marx, "Backtesting Strategies Based on Multiple Signals":
  <https://www.nber.org/papers/w21329>
- Backtests are sensitive to lookahead, transaction costs, and realistic
  execution assumptions. See QuantStart's overview of backtesting biases:
  <https://www.quantstart.com/articles/Successful-Backtesting-of-Algorithmic-Trading-Strategies-Part-I/>
- TWSE lists ETF transaction tax as `0.1%`, commission capped at `0.1425%`,
  and bond ETF transaction tax suspended through 2026:
  <https://www.twse.com.tw/en/products/securities/etf/overview/rules.html>

## Bottom Line

The outside review correctly identified real issues: raw TAIEX is a questionable
signal during ex-dividend season, midpoint reset is structurally suspicious,
and rigid cooldowns can miss the strongest signal. After fixing and testing
those concerns, the tactical layer still does not produce robust holdout Sharpe
alpha versus a simple static core/bullet allocation.
