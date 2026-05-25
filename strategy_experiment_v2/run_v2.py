"""Run single-ETF tactical cash-deployment experiments.

Each ticker is tested independently against cash.  There is no cross-ETF
allocation and no currency conversion.
"""
from __future__ import annotations

import argparse
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


ROOT_DIR = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT_DIR / "strategy_experiment_v2"
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

INITIAL_CAPITAL = 1.0
COMMISSION = 0.001425
TW_ETF_SELL_TAX = 0.001

CORE_PCTS = [0.0, 0.3, 0.5, 0.6, 0.7, 0.8]
TIER_COUNTS = [5, 10]
LOOKBACKS = [60, 120, 252, 504]
EXECUTIONS = ["weekly", "monthly"]
COOLDOWNS = [0, 5, 20]

FIRE_CONFIGS = {
    "F1_shallow": [40, 30, 20, 10, 5],
    "F2_standard": [30, 20, 15, 10, 5],
    "F3_deep": [20, 12, 7, 3, 1],
    "F4_crash_only": [10, 7, 5, 3, 1],
}

RETRIEVE_CONFIGS = {
    "R1_slow": [90, 94, 96, 98, 99],
    "R2_standard": [80, 88, 93, 97, 99],
    "R3_fast": [70, 80, 85, 90, 95],
    "R4_extreme_only": [95, 97, 98, 99, 99.5],
}

FIRE_SIGNALS = ["percentile_low", "drawdown_from_high", "rsi_low", "ma_stretch_low"]
RETRIEVE_SIGNALS = ["none", "percentile_high", "recovery_from_low", "rsi_high", "ma_stretch_high"]

STATIC_EXPOSURES = [0.5, 0.7, 0.8]


@dataclass(frozen=True)
class StrategySpec:
    name: str
    family: str
    core_pct: float | None = None
    tiers: int | None = None
    lookback: int | None = None
    execution: str | None = None
    cooldown: int | None = None
    fire_signal: str | None = None
    fire_config: str | None = None
    retrieve_signal: str | None = None
    retrieve_config: str | None = None


def yahoo_symbol(ticker: str) -> str:
    if ticker == "0050":
        return "0050.TW"
    return ticker


def load_prices(ticker: str, years: int) -> pd.Series:
    import yfinance as yf

    cache_dir = OUT_DIR / ".yfinance_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        yf.set_tz_cache_location(str(cache_dir))
    except Exception:
        pass

    end = pd.Timestamp.today().normalize() + pd.Timedelta(days=1)
    start = end - pd.DateOffset(years=years + 1)
    symbol = yahoo_symbol(ticker)
    df = yf.download(
        symbol,
        start=start.date().isoformat(),
        end=end.date().isoformat(),
        interval="1d",
        auto_adjust=False,
        actions=True,
        progress=False,
        threads=False,
    )
    if df is None or df.empty:
        raise RuntimeError(f"yfinance returned no data for {ticker} ({symbol})")
    if isinstance(df.columns, pd.MultiIndex):
        if symbol in df.columns.get_level_values(0):
            df = df[symbol]
        elif symbol in df.columns.get_level_values(-1):
            df = df.xs(symbol, axis=1, level=-1)
        else:
            df.columns = df.columns.get_level_values(0)
    col = "Adj Close" if "Adj Close" in df.columns else "Close"
    prices = df[col].dropna().astype(float)
    prices.index = pd.to_datetime(prices.index).tz_localize(None).normalize()
    latest = prices.index.max()
    window_start = latest - pd.DateOffset(years=years)
    prices = prices[prices.index >= window_start]
    prices.name = ticker
    if len(prices) < 252:
        raise RuntimeError(f"Too few rows for {ticker} {years}y: {len(prices)}")
    return prices


def rsi(series: pd.Series, window: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(window).mean()
    loss = (-delta.clip(upper=0)).rolling(window).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def percentile_rank(values: np.ndarray, current: float) -> float:
    return float((values <= current).sum()) / len(values) * 100.0


def expand_thresholds(base: list[float], tiers: int) -> list[float]:
    if tiers == len(base):
        return [float(x) for x in base]
    x_old = np.linspace(0, 1, len(base))
    x_new = np.linspace(0, 1, tiers)
    return [float(x) for x in np.interp(x_new, x_old, np.array(base, dtype=float))]


def signal_level(
    prices: pd.Series,
    i: int,
    lookback: int,
    signal: str,
    config_values: list[float],
    is_fire: bool,
) -> int:
    if i < lookback or i <= 0:
        return 0
    hist = prices.iloc[i - lookback : i]
    cur = float(prices.iloc[i - 1])
    thresholds = config_values

    if signal in ("percentile_low", "percentile_high"):
        rank = percentile_rank(hist.to_numpy(dtype=float), cur)
        if is_fire:
            return sum(rank <= t for t in thresholds)
        return sum(rank >= t for t in thresholds)

    if signal == "drawdown_from_high":
        high = float(hist.max())
        value = (1.0 - cur / high) * 100.0 if high > 0 else 0.0
        return sum(value >= t for t in thresholds)

    if signal == "recovery_from_low":
        low = float(hist.min())
        value = (cur / low - 1.0) * 100.0 if low > 0 else 0.0
        return sum(value >= t for t in thresholds)

    if signal in ("rsi_low", "rsi_high"):
        r = float(rsi(prices.iloc[:i]).iloc[-1])
        if math.isnan(r):
            return 0
        if is_fire:
            return sum(r <= t for t in thresholds)
        return sum(r >= t for t in thresholds)

    if signal in ("ma_stretch_low", "ma_stretch_high"):
        ma_window = min(200, max(20, lookback))
        ma = float(prices.iloc[:i].rolling(ma_window).mean().iloc[-1])
        if math.isnan(ma) or ma <= 0:
            return 0
        stretch = (cur / ma - 1.0) * 100.0
        if is_fire:
            return sum(stretch <= -abs(t) for t in thresholds)
        return sum(stretch >= t for t in thresholds)

    return 0


def signal_thresholds(signal: str, config_name: str, tiers: int, is_fire: bool) -> list[float]:
    if is_fire:
        base = FIRE_CONFIGS[config_name]
        if signal == "drawdown_from_high":
            scale = {
                "F1_shallow": [5, 8, 12, 16, 20],
                "F2_standard": [8, 12, 16, 20, 25],
                "F3_deep": [12, 18, 24, 30, 35],
                "F4_crash_only": [15, 22, 30, 38, 45],
            }[config_name]
            return expand_thresholds(scale, tiers)
        if signal == "rsi_low":
            scale = {
                "F1_shallow": [45, 40, 35, 30, 25],
                "F2_standard": [40, 35, 30, 25, 20],
                "F3_deep": [35, 30, 25, 20, 15],
                "F4_crash_only": [30, 25, 20, 15, 10],
            }[config_name]
            return expand_thresholds(scale, tiers)
        if signal == "ma_stretch_low":
            scale = {
                "F1_shallow": [2, 4, 6, 8, 10],
                "F2_standard": [4, 6, 8, 12, 16],
                "F3_deep": [6, 10, 15, 20, 25],
                "F4_crash_only": [10, 15, 20, 25, 35],
            }[config_name]
            return expand_thresholds(scale, tiers)
        return expand_thresholds(base, tiers)

    base = RETRIEVE_CONFIGS[config_name]
    if signal == "recovery_from_low":
        scale = {
            "R1_slow": [15, 20, 25, 30, 35],
            "R2_standard": [10, 15, 20, 25, 30],
            "R3_fast": [6, 10, 15, 20, 25],
            "R4_extreme_only": [25, 30, 35, 40, 50],
        }[config_name]
        return expand_thresholds(scale, tiers)
    if signal == "rsi_high":
        scale = {
            "R1_slow": [70, 75, 80, 85, 90],
            "R2_standard": [65, 70, 75, 80, 85],
            "R3_fast": [60, 65, 70, 75, 80],
            "R4_extreme_only": [80, 85, 90, 95, 98],
        }[config_name]
        return expand_thresholds(scale, tiers)
    if signal == "ma_stretch_high":
        scale = {
            "R1_slow": [12, 16, 20, 25, 30],
            "R2_standard": [8, 12, 16, 20, 25],
            "R3_fast": [5, 8, 12, 16, 20],
            "R4_extreme_only": [18, 24, 30, 36, 45],
        }[config_name]
        return expand_thresholds(scale, tiers)
    return expand_thresholds(base, tiers)


def is_execution_day(dates: pd.DatetimeIndex, i: int, mode: str) -> bool:
    if i == 0:
        return True
    cur = dates[i]
    nxt = dates[i + 1] if i + 1 < len(dates) else None
    if mode == "weekly":
        return cur.weekday() == 4 or (nxt is not None and nxt.isocalendar().week != cur.isocalendar().week)
    if mode == "monthly":
        return nxt is None or nxt.month != cur.month or nxt.year != cur.year
    return False


def sell_tax(ticker: str) -> float:
    return TW_ETF_SELL_TAX if ticker == "0050" else 0.0


def apply_rebalance_cost(value: float, old_exp: float, new_exp: float, ticker: str) -> tuple[float, float]:
    delta = new_exp - old_exp
    traded = abs(delta) * value
    if traded <= 0:
        return value, 0.0
    rate = COMMISSION + (sell_tax(ticker) if delta < 0 else 0.0)
    cost = traded * rate
    return value - cost, cost


def metrics(curve: pd.Series, costs: float, trades: int, spec: StrategySpec) -> dict:
    rets = curve.pct_change().dropna()
    days = max((curve.index[-1] - curve.index[0]).days, 1)
    total_return = curve.iloc[-1] / curve.iloc[0] - 1.0
    cagr = (curve.iloc[-1] / curve.iloc[0]) ** (365.25 / days) - 1.0
    vol = rets.std() * math.sqrt(252) if len(rets) > 1 else 0.0
    sharpe = (rets.mean() * 252) / vol if vol > 0 else 0.0
    downside = rets[rets < 0].std() * math.sqrt(252)
    sortino = (rets.mean() * 252) / downside if downside and downside > 0 else 0.0
    dd = curve / curve.cummax() - 1.0
    max_dd = float(dd.min())
    calmar = cagr / abs(max_dd) if max_dd < 0 else 0.0
    worst_252 = (curve / curve.shift(252) - 1.0).dropna().min()
    return {
        "strategy": spec.name,
        "family": spec.family,
        "total_return_pct": total_return * 100,
        "cagr_pct": cagr * 100,
        "ann_vol_pct": vol * 100,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_dd_pct": max_dd * 100,
        "calmar": calmar,
        "worst_1y_pct": float(worst_252 * 100) if pd.notna(worst_252) else np.nan,
        "trade_count": trades,
        "cost_drag_pct": costs * 100,
        "core_pct": spec.core_pct,
        "tiers": spec.tiers,
        "lookback": spec.lookback,
        "execution": spec.execution,
        "cooldown": spec.cooldown,
        "fire_signal": spec.fire_signal,
        "fire_config": spec.fire_config,
        "retrieve_signal": spec.retrieve_signal,
        "retrieve_config": spec.retrieve_config,
    }


def simulate(prices: pd.Series, ticker: str, spec: StrategySpec) -> tuple[pd.Series, float, int]:
    prices = prices.dropna()
    dates = prices.index
    asset_rets = prices.pct_change().fillna(0.0).to_numpy()
    curve = np.empty(len(prices), dtype=float)
    value = INITIAL_CAPITAL
    exposure = 0.0
    costs = 0.0
    trades = 0

    if spec.family == "all_in":
        value, cost = apply_rebalance_cost(value, exposure, 1.0, ticker)
        costs += cost
        exposure = 1.0
        for i in range(len(prices)):
            if i > 0:
                value *= 1.0 + exposure * asset_rets[i]
            curve[i] = value
        return pd.Series(curve, index=dates), costs, 1

    dca_next_month = None
    active_tiers = 0
    fire_cd_until = -1
    retrieve_cd_until = -1

    for i in range(len(prices)):
        if i > 0:
            value *= 1.0 + exposure * asset_rets[i]

        target = exposure
        should_rebalance = False

        if spec.family == "dca_5pct_monthly":
            if i == 0 or dates[i].month != dca_next_month:
                target = min(1.0, exposure + 0.05)
                dca_next_month = (dates[i] + pd.DateOffset(months=1)).month
                should_rebalance = target != exposure

        elif spec.family == "static":
            target = float(spec.core_pct or 0.0)
            should_rebalance = is_execution_day(dates, i, "monthly")

        elif spec.family == "tactical":
            if is_execution_day(dates, i, spec.execution or "monthly") and i > 0:
                core = float(spec.core_pct or 0.0)
                tiers = int(spec.tiers or 5)
                fire_thresholds = signal_thresholds(
                    spec.fire_signal or "percentile_low",
                    spec.fire_config or "F2_standard",
                    tiers,
                    True,
                )
                fire_level = signal_level(prices, i, int(spec.lookback or 252), spec.fire_signal or "percentile_low", fire_thresholds, True)
                if fire_level > active_tiers and i >= fire_cd_until:
                    active_tiers = fire_level
                    fire_cd_until = i + int(spec.cooldown or 0)

                if spec.retrieve_signal != "none":
                    retrieve_thresholds = signal_thresholds(
                        spec.retrieve_signal or "percentile_high",
                        spec.retrieve_config or "R2_standard",
                        tiers,
                        False,
                    )
                    retrieve_level = signal_level(prices, i, int(spec.lookback or 252), spec.retrieve_signal or "percentile_high", retrieve_thresholds, False)
                    if retrieve_level > 0 and i >= retrieve_cd_until:
                        active_tiers = min(active_tiers, max(0, tiers - retrieve_level))
                        retrieve_cd_until = i + int(spec.cooldown or 0)

                bullet = 1.0 - core
                target = core + bullet * active_tiers / tiers
                should_rebalance = target != exposure

        if should_rebalance:
            new_value, cost = apply_rebalance_cost(value, exposure, target, ticker)
            if cost > 0:
                trades += 1
            value = new_value
            costs += cost
            exposure = target

        curve[i] = value

    return pd.Series(curve, index=dates), costs, trades


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


def train_holdout_metrics(prices: pd.Series, ticker: str, spec: StrategySpec) -> dict:
    split = int(len(prices) * 0.6)
    train = prices.iloc[:split]
    holdout = prices.iloc[split:]
    train_curve, train_costs, train_trades = simulate(train, ticker, spec)
    hold_curve, hold_costs, hold_trades = simulate(holdout, ticker, spec)
    train_m = metrics(train_curve, train_costs, train_trades, spec)
    hold_m = metrics(hold_curve, hold_costs, hold_trades, spec)
    return {
        "train_cagr_pct": train_m["cagr_pct"],
        "train_sharpe": train_m["sharpe"],
        "train_max_dd_pct": train_m["max_dd_pct"],
        "holdout_cagr_pct": hold_m["cagr_pct"],
        "holdout_sharpe": hold_m["sharpe"],
        "holdout_max_dd_pct": hold_m["max_dd_pct"],
        "holdout_total_return_pct": hold_m["total_return_pct"],
    }


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:d}h{m:02d}m{s:02d}s"
    if m:
        return f"{m:d}m{s:02d}s"
    return f"{s:d}s"


def print_progress(label: str, idx: int, total: int, start_time: float, force_newline: bool = False) -> None:
    pct = idx / total if total else 1.0
    elapsed = time.time() - start_time
    eta = (elapsed / pct - elapsed) if pct > 0 else 0.0
    width = 28
    filled = min(width, int(width * pct))
    bar = "#" * filled + "." * (width - filled)
    msg = (
        f"\r{label} [{bar}] {idx:,}/{total:,} "
        f"{pct * 100:5.1f}% elapsed {format_duration(elapsed)} ETA {format_duration(eta)}"
    )
    print(msg, end="\n" if force_newline else "", flush=True)


def run_one(ticker: str, years: int, specs: list[StrategySpec], progress_every: int) -> pd.DataFrame:
    prices = load_prices(ticker, years)
    rows = []
    label = f"[{ticker} {years}y]"
    print(f"{label} rows={len(prices)} {prices.index.min().date()} -> {prices.index.max().date()}")
    start_time = time.time()
    print_progress(label, 0, len(specs), start_time)
    for idx, spec in enumerate(specs, 1):
        curve, costs, trades = simulate(prices, ticker, spec)
        row = metrics(curve, costs, trades, spec)
        row.update(train_holdout_metrics(prices, ticker, spec))
        row.update(
            {
                "ticker": ticker,
                "years": years,
                "start": prices.index.min().date().isoformat(),
                "end": prices.index.max().date().isoformat(),
                "n_days": len(prices),
            }
        )
        rows.append(row)
        if idx % progress_every == 0 or idx == len(specs):
            print_progress(label, idx, len(specs), start_time, force_newline=idx == len(specs))
    return pd.DataFrame(rows)


def summarize(results: pd.DataFrame) -> pd.DataFrame:
    out = []
    group_cols = ["ticker", "years"]
    for (ticker, years), g in results.groupby(group_cols):
        baseline = g[g["strategy"] == "all_in"].iloc[0]
        dca = g[g["strategy"] == "dca_5pct_monthly"].iloc[0]
        candidates = g.copy()
        candidates["robust_score"] = (
            candidates["holdout_cagr_pct"].rank(ascending=False)
            + candidates["holdout_sharpe"].rank(ascending=False)
            + candidates["calmar"].rank(ascending=False)
            + candidates["max_dd_pct"].rank(ascending=False)
            + candidates["trade_count"].rank(ascending=True) * 0.25
        )
        best_robust = candidates.sort_values("robust_score").iloc[0]
        best_holdout = candidates.sort_values("holdout_sharpe", ascending=False).iloc[0]
        best_cagr = candidates.sort_values("holdout_cagr_pct", ascending=False).iloc[0]
        for label, row in [
            ("all_in", baseline),
            ("dca_5pct_monthly", dca),
            ("best_robust", best_robust),
            ("best_holdout_sharpe", best_holdout),
            ("best_holdout_cagr", best_cagr),
        ]:
            out.append(
                {
                    "ticker": ticker,
                    "years": years,
                    "selection": label,
                    "strategy": row["strategy"],
                    "family": row["family"],
                    "total_return_pct": row["total_return_pct"],
                    "cagr_pct": row["cagr_pct"],
                    "sharpe": row["sharpe"],
                    "max_dd_pct": row["max_dd_pct"],
                    "holdout_total_return_pct": row["holdout_total_return_pct"],
                    "holdout_cagr_pct": row["holdout_cagr_pct"],
                    "holdout_sharpe": row["holdout_sharpe"],
                    "holdout_max_dd_pct": row["holdout_max_dd_pct"],
                    "trade_count": row["trade_count"],
                    "cost_drag_pct": row["cost_drag_pct"],
                    "core_pct": row["core_pct"],
                    "lookback": row["lookback"],
                    "execution": row["execution"],
                    "fire_signal": row["fire_signal"],
                    "fire_config": row["fire_config"],
                    "retrieve_signal": row["retrieve_signal"],
                    "retrieve_config": row["retrieve_config"],
                }
            )
    return pd.DataFrame(out)


def write_top_report(summary: pd.DataFrame, results: pd.DataFrame) -> None:
    lines = ["Strategy Experiment V2", "======================", ""]
    for (ticker, years), g in summary.groupby(["ticker", "years"]):
        lines.append(f"{ticker} / {years}y")
        lines.append("-" * (len(lines[-1])))
        for _, row in g.iterrows():
            lines.append(
                f"{row['selection']}: {row['strategy']} | "
                f"holdout CAGR {row['holdout_cagr_pct']:.2f}% | "
                f"holdout Sharpe {row['holdout_sharpe']:.2f} | "
                f"holdout DD {row['holdout_max_dd_pct']:.2f}% | "
                f"trades {row['trade_count']:.0f}"
            )
        lines.append("")

        all_in = g[g["selection"] == "all_in"].iloc[0]
        best = g[g["selection"] == "best_robust"].iloc[0]
        verdict = "PASS" if best["holdout_sharpe"] > all_in["holdout_sharpe"] and best["holdout_cagr_pct"] >= all_in["holdout_cagr_pct"] else "FAIL"
        lines.append(
            f"Robust tactical vs all-in: {verdict}. "
            f"All-in holdout Sharpe {all_in['holdout_sharpe']:.2f}, "
            f"best robust {best['holdout_sharpe']:.2f}."
        )
        lines.append("")

    (OUT_DIR / "top_v2.txt").write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", nargs="+", default=["0050", "SPY", "QQQ"])
    parser.add_argument("--years", nargs="+", type=int, default=[5, 10])
    parser.add_argument("--limit-specs", type=int, default=0, help="Debug: only run first N specs")
    parser.add_argument("--progress-every", type=int, default=250, help="Refresh progress every N strategies")
    args = parser.parse_args(argv)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    specs = make_specs()
    if args.limit_specs:
        specs = specs[: args.limit_specs]
    print(f"Specs: {len(specs):,}")

    frames = []
    for ticker in args.tickers:
        for years in args.years:
            frames.append(run_one(ticker, years, specs, max(1, args.progress_every)))

    results = pd.concat(frames, ignore_index=True)
    summary = summarize(results)
    results.to_csv(OUT_DIR / "results_v2_all.csv", index=False)
    summary.to_csv(OUT_DIR / "summary_v2.csv", index=False)
    write_top_report(summary, results)
    print(f"Wrote {OUT_DIR / 'results_v2_all.csv'}")
    print(f"Wrote {OUT_DIR / 'summary_v2.csv'}")
    print(f"Wrote {OUT_DIR / 'top_v2.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
