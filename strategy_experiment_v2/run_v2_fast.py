"""Fast Numba engine for strategy_experiment_v2.

This script keeps the same strategy grid idea as run_v2.py, but moves the hot
path out of Python:

- precompute every signal-level time series once per ticker/window
- encode strategy specs as numeric arrays
- evaluate all strategies in one Numba parallel loop

Run:
    python -m strategy_experiment_v2.run_v2_fast --tickers 0050 --years 5 10
"""
from __future__ import annotations

import argparse
import math
import sys
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd
from numba import njit, prange, set_num_threads

from strategy_experiment_v2.run_v2 import (
    COMMISSION,
    CORE_PCTS,
    COOLDOWNS,
    EXECUTIONS,
    FIRE_CONFIGS,
    FIRE_SIGNALS,
    INITIAL_CAPITAL,
    LOOKBACKS,
    OUT_DIR,
    RETRIEVE_CONFIGS,
    RETRIEVE_SIGNALS,
    STATIC_EXPOSURES,
    TIER_COUNTS,
    TW_ETF_SELL_TAX,
    StrategySpec,
    expand_thresholds,
    load_prices,
    signal_thresholds,
    summarize,
    write_top_report,
)


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

FAMILY_CODE = {"all_in": 0, "dca_5pct_monthly": 1, "static": 2, "tactical": 3}
EXEC_CODE = {"weekly": 0, "monthly": 1}


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:d}h{m:02d}m{s:02d}s"
    if m:
        return f"{m:d}m{s:02d}s"
    return f"{s:d}s"


def make_specs() -> list[StrategySpec]:
    specs = [
        StrategySpec("all_in", "all_in"),
        StrategySpec("dca_5pct_monthly", "dca_5pct_monthly"),
    ]
    for exposure in STATIC_EXPOSURES:
        specs.append(StrategySpec(f"static_{int(exposure * 100)}", "static", core_pct=exposure))

    for core in CORE_PCTS:
        for tiers in TIER_COUNTS:
            for lookback in LOOKBACKS:
                for execution in EXECUTIONS:
                    for cooldown in COOLDOWNS:
                        for fire_signal in FIRE_SIGNALS:
                            for fire_config in FIRE_CONFIGS:
                                specs.append(
                                    StrategySpec(
                                        name=(
                                            f"tactical_core{int(core*100)}_t{tiers}_{fire_signal}_{fire_config}_"
                                            f"no_retrieve_lb{lookback}_{execution}_cd{cooldown}"
                                        ),
                                        family="tactical",
                                        core_pct=core,
                                        tiers=tiers,
                                        lookback=lookback,
                                        execution=execution,
                                        cooldown=cooldown,
                                        fire_signal=fire_signal,
                                        fire_config=fire_config,
                                        retrieve_signal="none",
                                        retrieve_config="none",
                                    )
                                )
                                for retrieve_signal in RETRIEVE_SIGNALS:
                                    if retrieve_signal == "none":
                                        continue
                                    for retrieve_config in RETRIEVE_CONFIGS:
                                        specs.append(
                                            StrategySpec(
                                                name=(
                                                    f"tactical_core{int(core*100)}_t{tiers}_{fire_signal}_{fire_config}_"
                                                    f"{retrieve_signal}_{retrieve_config}_lb{lookback}_{execution}_cd{cooldown}"
                                                ),
                                                family="tactical",
                                                core_pct=core,
                                                tiers=tiers,
                                                lookback=lookback,
                                                execution=execution,
                                                cooldown=cooldown,
                                                fire_signal=fire_signal,
                                                fire_config=fire_config,
                                                retrieve_signal=retrieve_signal,
                                                retrieve_config=retrieve_config,
                                            )
                                        )
    return specs


def rsi_array(prices: np.ndarray, window: int = 14) -> np.ndarray:
    out = np.full(len(prices), np.nan, dtype=np.float64)
    gains = np.zeros(len(prices), dtype=np.float64)
    losses = np.zeros(len(prices), dtype=np.float64)
    diff = np.diff(prices, prepend=prices[0])
    gains[diff > 0] = diff[diff > 0]
    losses[diff < 0] = -diff[diff < 0]
    for i in range(window, len(prices)):
        avg_gain = gains[i - window + 1 : i + 1].mean()
        avg_loss = losses[i - window + 1 : i + 1].mean()
        if avg_loss == 0:
            out[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            out[i] = 100.0 - 100.0 / (1.0 + rs)
    return out


def ma_array(prices: np.ndarray, window: int) -> np.ndarray:
    s = pd.Series(prices)
    return s.rolling(window).mean().to_numpy(dtype=np.float64)


def precompute_level(
    prices: np.ndarray,
    signal: str,
    thresholds: list[float],
    lookback: int,
    is_fire: bool,
    rsi14: np.ndarray,
    ma_cache: dict[int, np.ndarray],
) -> np.ndarray:
    n = len(prices)
    levels = np.zeros(n, dtype=np.int16)
    th = np.asarray(thresholds, dtype=np.float64)
    for i in range(1, n):
        if i < lookback:
            continue
        hist = prices[i - lookback : i]
        cur = prices[i - 1]
        value = 0.0
        if signal in ("percentile_low", "percentile_high"):
            rank = (hist <= cur).sum() / len(hist) * 100.0
            levels[i] = int((rank <= th).sum() if is_fire else (rank >= th).sum())
            continue
        if signal == "drawdown_from_high":
            high = hist.max()
            value = (1.0 - cur / high) * 100.0 if high > 0 else 0.0
            levels[i] = int((value >= th).sum())
            continue
        if signal == "recovery_from_low":
            low = hist.min()
            value = (cur / low - 1.0) * 100.0 if low > 0 else 0.0
            levels[i] = int((value >= th).sum())
            continue
        if signal in ("rsi_low", "rsi_high"):
            value = rsi14[i - 1]
            if not np.isfinite(value):
                continue
            levels[i] = int((value <= th).sum() if is_fire else (value >= th).sum())
            continue
        if signal in ("ma_stretch_low", "ma_stretch_high"):
            ma_window = min(200, max(20, lookback))
            ma = ma_cache[ma_window][i - 1]
            if not np.isfinite(ma) or ma <= 0:
                continue
            value = (cur / ma - 1.0) * 100.0
            levels[i] = int((value <= -np.abs(th)).sum() if is_fire else (value >= th).sum())
    return levels


def execution_masks(dates: pd.DatetimeIndex) -> np.ndarray:
    n = len(dates)
    masks = np.zeros((2, n), dtype=np.bool_)
    iso_weeks = pd.Series(dates).dt.isocalendar().week.to_numpy()
    months = pd.Series(dates).dt.month.to_numpy()
    years = pd.Series(dates).dt.year.to_numpy()
    weekdays = pd.Series(dates).dt.weekday.to_numpy()
    for i in range(n):
        if i == n - 1:
            masks[0, i] = True
            masks[1, i] = True
        else:
            masks[0, i] = weekdays[i] == 4 or iso_weeks[i + 1] != iso_weeks[i]
            masks[1, i] = months[i + 1] != months[i] or years[i + 1] != years[i]
    masks[:, 0] = True
    return masks


def build_level_matrices(prices: np.ndarray, specs: list[StrategySpec]) -> tuple[np.ndarray, np.ndarray, dict, dict]:
    rsi14 = rsi_array(prices, 14)
    ma_cache = {w: ma_array(prices, w) for w in sorted({20, 60, 120, 200})}
    fire_keys: dict[tuple, int] = {}
    retrieve_keys: dict[tuple, int] = {("none", "none", 0, 0): 0}
    fire_arrays: list[np.ndarray] = []
    retrieve_arrays: list[np.ndarray] = [np.zeros(len(prices), dtype=np.int16)]

    for spec in specs:
        if spec.family != "tactical":
            continue
        f_key = (spec.fire_signal, spec.fire_config, int(spec.tiers or 5), int(spec.lookback or 252))
        if f_key not in fire_keys:
            thresholds = signal_thresholds(spec.fire_signal or "percentile_low", spec.fire_config or "F2_standard", int(spec.tiers or 5), True)
            fire_keys[f_key] = len(fire_arrays)
            fire_arrays.append(precompute_level(prices, spec.fire_signal or "percentile_low", thresholds, int(spec.lookback or 252), True, rsi14, ma_cache))
        if spec.retrieve_signal != "none":
            r_key = (spec.retrieve_signal, spec.retrieve_config, int(spec.tiers or 5), int(spec.lookback or 252))
            if r_key not in retrieve_keys:
                thresholds = signal_thresholds(spec.retrieve_signal or "percentile_high", spec.retrieve_config or "R2_standard", int(spec.tiers or 5), False)
                retrieve_keys[r_key] = len(retrieve_arrays)
                retrieve_arrays.append(precompute_level(prices, spec.retrieve_signal or "percentile_high", thresholds, int(spec.lookback or 252), False, rsi14, ma_cache))

    return np.vstack(fire_arrays), np.vstack(retrieve_arrays), fire_keys, retrieve_keys


def encode_specs(specs: list[StrategySpec], fire_keys: dict, retrieve_keys: dict) -> dict[str, np.ndarray]:
    n = len(specs)
    family = np.zeros(n, dtype=np.int16)
    core = np.zeros(n, dtype=np.float64)
    tiers = np.ones(n, dtype=np.int16)
    execution = np.zeros(n, dtype=np.int16)
    cooldown = np.zeros(n, dtype=np.int16)
    fire_idx = np.zeros(n, dtype=np.int16)
    retrieve_idx = np.zeros(n, dtype=np.int16)
    for i, spec in enumerate(specs):
        family[i] = FAMILY_CODE[spec.family]
        core[i] = float(spec.core_pct or 0.0)
        tiers[i] = int(spec.tiers or 1)
        execution[i] = EXEC_CODE.get(spec.execution or "monthly", 1)
        cooldown[i] = int(spec.cooldown or 0)
        if spec.family == "tactical":
            f_key = (spec.fire_signal, spec.fire_config, int(spec.tiers or 5), int(spec.lookback or 252))
            fire_idx[i] = fire_keys[f_key]
            if spec.retrieve_signal != "none":
                r_key = (spec.retrieve_signal, spec.retrieve_config, int(spec.tiers or 5), int(spec.lookback or 252))
                retrieve_idx[i] = retrieve_keys[r_key]
    return {
        "family": family,
        "core": core,
        "tiers": tiers,
        "execution": execution,
        "cooldown": cooldown,
        "fire_idx": fire_idx,
        "retrieve_idx": retrieve_idx,
    }


@njit
def simulate_metrics_range(
    rets: np.ndarray,
    exec_masks: np.ndarray,
    fire_levels: np.ndarray,
    retrieve_levels: np.ndarray,
    family: np.ndarray,
    core: np.ndarray,
    tiers: np.ndarray,
    execution: np.ndarray,
    cooldown: np.ndarray,
    fire_idx: np.ndarray,
    retrieve_idx: np.ndarray,
    start: int,
    end: int,
    sell_tax: float,
    out: np.ndarray,
    row: int,
) -> None:
    value = INITIAL_CAPITAL
    exposure = 0.0
    costs = 0.0
    trades = 0
    active_tiers = 0
    fire_cd_until = -1
    retrieve_cd_until = -1

    n = end - start
    curve = np.empty(n, dtype=np.float64)
    for k in range(n):
        i = start + k
        if k > 0:
            value *= 1.0 + exposure * rets[i]
        target = exposure
        should_rebalance = False
        fam = family[row]

        if fam == 0:
            if k == 0:
                target = 1.0
                should_rebalance = True
        elif fam == 1:
            if k == 0 or exec_masks[1, i]:
                target = exposure + 0.05
                if target > 1.0:
                    target = 1.0
                should_rebalance = target != exposure
        elif fam == 2:
            target = core[row]
            should_rebalance = exec_masks[1, i]
        else:
            if exec_masks[execution[row], i] and k > 0:
                f_level = fire_levels[fire_idx[row], i]
                if f_level > active_tiers and k >= fire_cd_until:
                    active_tiers = f_level
                    fire_cd_until = k + cooldown[row]
                ridx = retrieve_idx[row]
                if ridx > 0:
                    r_level = retrieve_levels[ridx, i]
                    if r_level > 0 and k >= retrieve_cd_until:
                        new_active = tiers[row] - r_level
                        if new_active < 0:
                            new_active = 0
                        if new_active < active_tiers:
                            active_tiers = new_active
                        retrieve_cd_until = k + cooldown[row]
                bullet = 1.0 - core[row]
                target = core[row] + bullet * active_tiers / tiers[row]
                should_rebalance = target != exposure

        if should_rebalance:
            delta = target - exposure
            traded = abs(delta) * value
            if traded > 0:
                rate = COMMISSION
                if delta < 0:
                    rate += sell_tax
                cost = traded * rate
                value -= cost
                costs += cost
                trades += 1
            exposure = target
        curve[k] = value

    total_return = curve[n - 1] / curve[0] - 1.0
    years = n / 252.0
    cagr = curve[n - 1] / curve[0]
    if cagr > 0 and years > 0:
        cagr = cagr ** (1.0 / years) - 1.0
    else:
        cagr = 0.0

    mean = 0.0
    m2 = 0.0
    down_m2 = 0.0
    down_n = 0
    ret_n = 0
    peak = curve[0]
    max_dd = 0.0
    worst_252 = 0.0
    has_worst = False
    for k in range(1, n):
        r = curve[k] / curve[k - 1] - 1.0
        ret_n += 1
        d = r - mean
        mean += d / ret_n
        m2 += d * (r - mean)
        if r < 0:
            down_n += 1
            down_m2 += r * r
        if curve[k] > peak:
            peak = curve[k]
        dd = curve[k] / peak - 1.0
        if dd < max_dd:
            max_dd = dd
        if k >= 252:
            wr = curve[k] / curve[k - 252] - 1.0
            if (not has_worst) or wr < worst_252:
                worst_252 = wr
                has_worst = True

    vol = 0.0
    sharpe = 0.0
    if ret_n > 1:
        vol = math.sqrt(m2 / (ret_n - 1)) * math.sqrt(252.0)
        if vol > 0:
            sharpe = mean * 252.0 / vol
    sortino = 0.0
    if down_n > 0:
        downside = math.sqrt(down_m2 / down_n) * math.sqrt(252.0)
        if downside > 0:
            sortino = mean * 252.0 / downside
    calmar = cagr / abs(max_dd) if max_dd < 0 else 0.0

    out[row, 0] = total_return * 100.0
    out[row, 1] = cagr * 100.0
    out[row, 2] = vol * 100.0
    out[row, 3] = sharpe
    out[row, 4] = sortino
    out[row, 5] = max_dd * 100.0
    out[row, 6] = calmar
    out[row, 7] = worst_252 * 100.0 if has_worst else np.nan
    out[row, 8] = trades
    out[row, 9] = costs * 100.0


@njit(parallel=True)
def evaluate_all(
    rets: np.ndarray,
    exec_masks: np.ndarray,
    fire_levels: np.ndarray,
    retrieve_levels: np.ndarray,
    family: np.ndarray,
    core: np.ndarray,
    tiers: np.ndarray,
    execution: np.ndarray,
    cooldown: np.ndarray,
    fire_idx: np.ndarray,
    retrieve_idx: np.ndarray,
    split: int,
    sell_tax: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    n_specs = len(family)
    full = np.empty((n_specs, 10), dtype=np.float64)
    train = np.empty((n_specs, 10), dtype=np.float64)
    holdout = np.empty((n_specs, 10), dtype=np.float64)
    for row in prange(n_specs):
        simulate_metrics_range(rets, exec_masks, fire_levels, retrieve_levels, family, core, tiers, execution, cooldown, fire_idx, retrieve_idx, 0, len(rets), sell_tax, full, row)
        simulate_metrics_range(rets, exec_masks, fire_levels, retrieve_levels, family, core, tiers, execution, cooldown, fire_idx, retrieve_idx, 0, split, sell_tax, train, row)
        simulate_metrics_range(rets, exec_masks, fire_levels, retrieve_levels, family, core, tiers, execution, cooldown, fire_idx, retrieve_idx, split, len(rets), sell_tax, holdout, row)
    return full, train, holdout


def result_frame(
    ticker: str,
    years: int,
    prices: pd.Series,
    specs: list[StrategySpec],
    full: np.ndarray,
    train: np.ndarray,
    holdout: np.ndarray,
) -> pd.DataFrame:
    rows = []
    columns = [
        "total_return_pct", "cagr_pct", "ann_vol_pct", "sharpe", "sortino",
        "max_dd_pct", "calmar", "worst_1y_pct", "trade_count", "cost_drag_pct",
    ]
    for i, spec in enumerate(specs):
        row = asdict(spec)
        row.update({columns[j]: full[i, j] for j in range(len(columns))})
        row.update(
            {
                "strategy": spec.name,
                "ticker": ticker,
                "years": years,
                "start": prices.index.min().date().isoformat(),
                "end": prices.index.max().date().isoformat(),
                "n_days": len(prices),
                "train_cagr_pct": train[i, 1],
                "train_sharpe": train[i, 3],
                "train_max_dd_pct": train[i, 5],
                "holdout_total_return_pct": holdout[i, 0],
                "holdout_cagr_pct": holdout[i, 1],
                "holdout_sharpe": holdout[i, 3],
                "holdout_max_dd_pct": holdout[i, 5],
            }
        )
        rows.append(row)
    return pd.DataFrame(rows)


def run_one_fast(ticker: str, years: int, specs: list[StrategySpec]) -> pd.DataFrame:
    t0 = time.time()
    prices = load_prices(ticker, years)
    print(f"[{ticker} {years}y] rows={len(prices)} {prices.index.min().date()} -> {prices.index.max().date()}")
    close = prices.to_numpy(dtype=np.float64)
    rets = np.zeros(len(close), dtype=np.float64)
    rets[1:] = close[1:] / close[:-1] - 1.0
    print("  precomputing signal levels...", flush=True)
    fire_levels, retrieve_levels, fire_keys, retrieve_keys = build_level_matrices(close, specs)
    encoded = encode_specs(specs, fire_keys, retrieve_keys)
    masks = execution_masks(prices.index)
    split = int(len(close) * 0.6)
    tax = TW_ETF_SELL_TAX if ticker == "0050" else 0.0
    print(
        f"  evaluating {len(specs):,} specs with numba "
        f"({fire_levels.shape[0]} fire signals, {retrieve_levels.shape[0]} retrieve signals)...",
        flush=True,
    )
    full, train, holdout = evaluate_all(
        rets,
        masks,
        fire_levels,
        retrieve_levels,
        encoded["family"],
        encoded["core"],
        encoded["tiers"],
        encoded["execution"],
        encoded["cooldown"],
        encoded["fire_idx"],
        encoded["retrieve_idx"],
        split,
        tax,
    )
    print(f"  done in {format_duration(time.time() - t0)}", flush=True)
    return result_frame(ticker, years, prices, specs, full, train, holdout)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", nargs="+", default=["0050", "SPY", "QQQ"])
    parser.add_argument("--years", nargs="+", type=int, default=[5, 10])
    parser.add_argument("--limit-specs", type=int, default=0)
    parser.add_argument("--threads", type=int, default=0, help="Numba threads. 0 = all available.")
    args = parser.parse_args(argv)

    if args.threads and args.threads > 0:
        set_num_threads(args.threads)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    specs = make_specs()
    if args.limit_specs:
        specs = specs[: args.limit_specs]
    print(f"Specs: {len(specs):,}")

    frames = []
    total_start = time.time()
    for ticker in args.tickers:
        for years in args.years:
            frames.append(run_one_fast(ticker, years, specs))

    results = pd.concat(frames, ignore_index=True)
    summary = summarize(results)
    results.to_csv(OUT_DIR / "results_v2_all.csv", index=False)
    summary.to_csv(OUT_DIR / "summary_v2.csv", index=False)
    write_top_report(summary, results)
    print(f"Wrote {OUT_DIR / 'results_v2_all.csv'}")
    print(f"Wrote {OUT_DIR / 'summary_v2.csv'}")
    print(f"Wrote {OUT_DIR / 'top_v2.txt'}")
    print(f"Total elapsed {format_duration(time.time() - total_start)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
