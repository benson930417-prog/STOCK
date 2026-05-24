"""Corrected tactical-rule audit.

This is intentionally separate from strategy_experiment/. It keeps the same
strategy idea, but fixes the main audit issues:

- Signals are generated from today's close and executed on the next trading day.
- Initial buy costs are included and performance is measured vs starting cash.
- Rebalance costs are modeled by traded leg, not by one rough blended rate.
- Sharpe/Sortino use excess daily returns over a configurable risk-free rate.
- The grid is selected on an in-sample train window and challenged on a later
  holdout window.
- Cooldowns track the strongest signal seen during the cooldown instead of
  forgetting it.

Run from repo root:
    python -m strategy_experiment_codex.codex_tactical_audit --years 2
"""
from __future__ import annotations

import argparse
import csv
import math
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
DB_PATH = ROOT_DIR / "data" / "etf_bench" / "etf_bench.sqlite"

INITIAL_CAPITAL = 1_000_000.0
N_TIERS = 5
COMMISSION = 0.001425
ETF_SELL_TAX = 0.001
DEPLOY_MIN = 0.0
DEPLOY_MAX = 1.0

FIRE_CONFIGS = {
    "A1_aggressive": [30, 20, 15, 10, 5],
    "A2_standard": [25, 15, 10, 5, 2],
    "A3_conservative": [20, 12, 7, 3, 1],
    "A4_xconservative": [15, 8, 4, 2, 1],
    "A5_narrow": [20, 16, 12, 8, 4],
    "A6_wide": [35, 22, 14, 8, 3],
}
RETRIEVE_CONFIGS = {
    "R1_aggressive": [70, 80, 85, 90, 95],
    "R2_standard": [75, 85, 90, 95, 98],
    "R3_conservative": [80, 88, 93, 97, 99],
    "R4_xconservative": [85, 92, 96, 98, 99],
    "R5_narrow": [80, 84, 88, 92, 96],
    "R6_wide": [65, 78, 86, 92, 97],
}
COOLDOWN_DAYS = [3, 5, 7]
LOOKBACKS = [60, 90]
CORE_PCTS = [0.30, 0.40, 0.50, 0.60, 0.70]


@dataclass
class SeriesBundle:
    taiex: pd.Series
    core: pd.Series
    bullet: pd.Series
    signal: pd.Series
    core_type: str
    bullet_type: str
    source: str


@dataclass
class BTResult:
    total_return_pct: float
    annual_return_pct: float
    sharpe_excess: float
    sortino_excess: float
    max_dd_pct: float
    n_trades: int
    n_fire: int
    n_retrieve: int
    final_value: float
    curve: pd.Series


@dataclass
class ConfigResult:
    fire_name: str
    retrieve_name: str
    cooldown: int
    lookback: int
    core_pct: float
    result: BTResult

    def key(self) -> str:
        return (
            f"core={int(self.core_pct * 100)}%/"
            f"{self.fire_name}/{self.retrieve_name}/"
            f"cd={self.cooldown}/win={self.lookback}"
        )


def _fund_type(ticker: str) -> str:
    with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as conn:
        row = conn.execute("SELECT fund_type FROM etfs WHERE ticker = ?", (ticker,)).fetchone()
    return str(row[0]) if row else ""


def _yahoo_symbol(ticker: str) -> str:
    if ticker.startswith("^"):
        return ticker
    with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as conn:
        row = conn.execute("SELECT yahoo_symbol, market FROM etfs WHERE ticker = ?", (ticker,)).fetchone()
    if row and row[0]:
        return str(row[0])
    suffix = ".TWO" if row and row[1] == "TPEx" else ".TW"
    return f"{ticker}{suffix}"


def _load_price(ticker: str, use_adj: bool, start: pd.Timestamp | None) -> pd.Series:
    col = "adj_close" if use_adj else "close"
    sql = f"SELECT date, {col} AS price FROM prices WHERE ticker = ? AND {col} IS NOT NULL"
    params: list[object] = [ticker]
    if start is not None:
        sql += " AND date >= ?"
        params.append(start.date().isoformat())
    sql += " ORDER BY date"
    with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as conn:
        df = pd.read_sql_query(sql, conn, params=params)
    if df.empty:
        raise ValueError(f"No price data for {ticker}")
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")["price"].astype(float).rename(ticker)


def _load_price_yf(ticker: str, use_adj: bool, start: pd.Timestamp) -> pd.Series:
    import yfinance as yf

    cache_dir = SCRIPT_DIR / ".yfinance_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        yf.set_tz_cache_location(str(cache_dir))
    except Exception:
        pass

    symbol = _yahoo_symbol(ticker)
    df = yf.download(
        symbol,
        start=start.date().isoformat(),
        interval="1d",
        auto_adjust=False,
        actions=False,
        progress=False,
        threads=False,
    )
    if df is None or df.empty:
        raise ValueError(f"yfinance returned no data for {ticker} ({symbol})")
    if isinstance(df.columns, pd.MultiIndex):
        if symbol in df.columns.get_level_values(0):
            df = df[symbol]
        elif symbol in df.columns.get_level_values(-1):
            df = df.xs(symbol, axis=1, level=-1)
        elif ticker in df.columns.get_level_values(0):
            df = df[ticker]
        elif ticker in df.columns.get_level_values(-1):
            df = df.xs(ticker, axis=1, level=-1)
        elif len(df.columns.levels) == 2:
            df.columns = df.columns.get_level_values(-1)
    col = "Adj Close" if use_adj and "Adj Close" in df.columns else "Close"
    out = df[col].dropna().astype(float)
    out.index = pd.to_datetime(out.index).tz_localize(None).normalize()
    return out.rename(ticker)


def load_bundle(core_ticker: str, bullet_ticker: str, years: float, signal_source: str) -> SeriesBundle:
    latest = _load_price("^TWII", use_adj=False, start=None).index.max()
    start = latest - pd.Timedelta(days=int(round(years * 365.25)))
    db_taiex = _load_price("^TWII", use_adj=False, start=start)
    use_yfinance = years > 2.1 and db_taiex.index.min() > start + pd.Timedelta(days=30)
    if use_yfinance:
        taiex = _load_price_yf("^TWII", use_adj=False, start=start)
        core = _load_price_yf(core_ticker, use_adj=True, start=start)
        bullet = _load_price_yf(bullet_ticker, use_adj=True, start=start)
        source = "yfinance"
    else:
        taiex = db_taiex
        core = _load_price(core_ticker, use_adj=True, start=start)
        bullet = _load_price(bullet_ticker, use_adj=True, start=start)
        source = "local_db"
    if signal_source == "core":
        signal = core.rename("signal")
    elif signal_source == "taiex":
        signal = taiex.rename("signal")
    else:
        raise ValueError("--signal-source must be taiex or core")
    df = pd.concat([taiex, core, bullet, signal], axis=1, join="inner").dropna()
    if len(df) < max(LOOKBACKS) + 30:
        raise ValueError(f"Aligned sample too short: {len(df)} rows")
    return SeriesBundle(
        taiex=df.iloc[:, 0],
        core=df.iloc[:, 1],
        bullet=df.iloc[:, 2],
        signal=df.iloc[:, 3],
        core_type=_fund_type(core_ticker),
        bullet_type=_fund_type(bullet_ticker),
        source=source,
    )


def sell_tax_for_fund(fund_type: str) -> float:
    # TWSE states bond ETF transaction tax is suspended through 2026.
    return 0.0 if fund_type == "bond" else ETF_SELL_TAX


def percentile_rank(window: np.ndarray, current: float) -> float:
    return float((window <= current).sum()) / len(window) * 100.0


def initial_shares(
    core_price: float,
    bullet_price: float,
    core_pct: float,
    initial_capital: float,
) -> tuple[float, float, float]:
    core_spend = initial_capital * core_pct
    bullet_spend = initial_capital * (1.0 - core_pct)
    core_buy_value = core_spend / (1.0 + COMMISSION)
    bullet_buy_value = bullet_spend / (1.0 + COMMISSION)
    cash = initial_capital - core_buy_value * (1.0 + COMMISSION) - bullet_buy_value * (1.0 + COMMISSION)
    return core_buy_value / core_price, bullet_buy_value / bullet_price, cash


def rebalance(
    core_shares: float,
    bullet_shares: float,
    cash: float,
    core_price: float,
    bullet_price: float,
    target_core_pct: float,
    core_sell_tax: float,
    bullet_sell_tax: float,
) -> tuple[float, float, float, bool]:
    core_value = core_shares * core_price
    bullet_value = bullet_shares * bullet_price
    total = core_value + bullet_value + cash
    target_core_value = total * target_core_pct
    delta_core = target_core_value - core_value
    if abs(delta_core) < total * 0.0001:
        return core_shares, bullet_shares, cash, False

    if delta_core > 0:
        buy_value = delta_core
        needed = buy_value * (1.0 + COMMISSION)
        if cash < needed:
            sell_net_needed = needed - cash
            sell_gross = sell_net_needed / (1.0 - COMMISSION - bullet_sell_tax)
            sell_gross = min(sell_gross, bullet_value)
            bullet_shares -= sell_gross / bullet_price
            cash += sell_gross * (1.0 - COMMISSION - bullet_sell_tax)
        buy_value = min(buy_value, cash / (1.0 + COMMISSION))
        core_shares += buy_value / core_price
        cash -= buy_value * (1.0 + COMMISSION)
    else:
        sell_gross = min(-delta_core, core_value)
        core_shares -= sell_gross / core_price
        cash += sell_gross * (1.0 - COMMISSION - core_sell_tax)
        buy_value = cash / (1.0 + COMMISSION)
        bullet_shares += buy_value / bullet_price
        cash -= buy_value * (1.0 + COMMISSION)

    return core_shares, bullet_shares, cash, True


def compute_metrics(
    curve: pd.Series,
    n_trades: int,
    n_fire: int,
    n_retrieve: int,
    initial_capital: float,
    risk_free_annual: float,
) -> BTResult:
    final = float(curve.iloc[-1])
    total_return = (final / initial_capital - 1.0) * 100.0
    annual_return = ((final / initial_capital) ** (252.0 / max(len(curve), 1)) - 1.0) * 100.0
    rets = curve.pct_change().dropna()
    daily_rf = (1.0 + risk_free_annual) ** (1.0 / 252.0) - 1.0
    excess = rets - daily_rf
    if len(excess) > 1 and excess.std() > 0:
        sharpe = float(excess.mean() / excess.std() * math.sqrt(252))
    else:
        sharpe = 0.0
    downside = excess[excess < 0]
    if len(downside) > 1 and downside.std() > 0:
        sortino = float(excess.mean() / downside.std() * math.sqrt(252))
    else:
        sortino = 0.0
    max_dd = float(((curve - curve.cummax()) / curve.cummax()).min()) * 100.0
    return BTResult(
        total_return_pct=total_return,
        annual_return_pct=annual_return,
        sharpe_excess=sharpe,
        sortino_excess=sortino,
        max_dd_pct=max_dd,
        n_trades=n_trades,
        n_fire=n_fire,
        n_retrieve=n_retrieve,
        final_value=final,
        curve=curve,
    )


def backtest_tactical(
    bundle: SeriesBundle,
    fire_thresholds: list[int],
    retrieve_thresholds: list[int],
    cooldown_days: int,
    lookback_days: int,
    core_pct: float,
    initial_capital: float,
    risk_free_annual: float,
    midpoint_reset: bool,
    track_cooldown_extremes: bool,
) -> BTResult:
    signal = bundle.signal.to_numpy()
    core = bundle.core.to_numpy()
    bullet = bundle.bullet.to_numpy()
    index = bundle.taiex.index
    core_sell_tax = sell_tax_for_fund(bundle.core_type)
    bullet_sell_tax = sell_tax_for_fund(bundle.bullet_type)

    core_shares, bullet_shares, cash = initial_shares(core[0], bullet[0], core_pct, initial_capital)
    fire_step = (1.0 - core_pct) / N_TIERS
    retrieve_step = core_pct / N_TIERS
    fire_active = retrieve_active = 0
    fire_cd_until = retrieve_cd_until = -1
    queued_fire = queued_retrieve = 0
    current_deployment = core_pct
    pending_deployment: float | None = None
    curve = np.empty(len(index), dtype=float)
    n_trades = n_fire = n_retrieve = 0

    for i in range(len(index)):
        if pending_deployment is not None and abs(pending_deployment - current_deployment) > 1e-6:
            core_shares, bullet_shares, cash, traded = rebalance(
                core_shares,
                bullet_shares,
                cash,
                core[i],
                bullet[i],
                pending_deployment,
                core_sell_tax,
                bullet_sell_tax,
            )
            if traded:
                n_trades += 1
                current_deployment = pending_deployment
        pending_deployment = None

        curve[i] = core_shares * core[i] + bullet_shares * bullet[i] + cash

        if i < lookback_days or i >= len(index) - 1:
            continue

        rank = percentile_rank(signal[i - lookback_days : i + 1], signal[i])
        target_fire = sum(1 for threshold in fire_thresholds if rank <= threshold)
        target_retrieve = sum(1 for threshold in retrieve_thresholds if rank >= threshold)

        effective_fire = max(target_fire, queued_fire if track_cooldown_extremes else 0)
        effective_retrieve = max(target_retrieve, queued_retrieve if track_cooldown_extremes else 0)

        if effective_fire > fire_active:
            if i > fire_cd_until:
                fire_active = effective_fire
                retrieve_active = 0
                queued_fire = queued_retrieve = 0
                fire_cd_until = i + cooldown_days
                n_fire += 1
            elif track_cooldown_extremes:
                queued_fire = max(queued_fire, effective_fire)
        if effective_retrieve > retrieve_active:
            if i > retrieve_cd_until:
                retrieve_active = effective_retrieve
                fire_active = 0
                queued_fire = queued_retrieve = 0
                retrieve_cd_until = i + cooldown_days
                n_retrieve += 1
            elif track_cooldown_extremes:
                queued_retrieve = max(queued_retrieve, effective_retrieve)

        if midpoint_reset and i > max(fire_cd_until, retrieve_cd_until):
            if fire_active > 0 and rank > 50:
                fire_active = 0
            if retrieve_active > 0 and rank < 50:
                retrieve_active = 0

        deployment = core_pct + fire_step * fire_active - retrieve_step * retrieve_active
        pending_deployment = max(DEPLOY_MIN, min(DEPLOY_MAX, deployment))

    return compute_metrics(pd.Series(curve, index=index), n_trades, n_fire, n_retrieve, initial_capital, risk_free_annual)


def backtest_static(
    bundle: SeriesBundle,
    core_pct: float,
    initial_capital: float,
    risk_free_annual: float,
    rebalance_every_n_days: int = 5,
) -> BTResult:
    core = bundle.core.to_numpy()
    bullet = bundle.bullet.to_numpy()
    index = bundle.core.index
    core_sell_tax = sell_tax_for_fund(bundle.core_type)
    bullet_sell_tax = sell_tax_for_fund(bundle.bullet_type)
    core_shares, bullet_shares, cash = initial_shares(core[0], bullet[0], core_pct, initial_capital)
    curve = np.empty(len(index), dtype=float)
    n_trades = 0
    for i in range(len(index)):
        if i > 0 and i % rebalance_every_n_days == 0:
            total = core_shares * core[i] + bullet_shares * bullet[i] + cash
            current_core_pct = (core_shares * core[i]) / total if total > 0 else core_pct
            if abs(current_core_pct - core_pct) > 0.005:
                core_shares, bullet_shares, cash, traded = rebalance(
                    core_shares,
                    bullet_shares,
                    cash,
                    core[i],
                    bullet[i],
                    core_pct,
                    core_sell_tax,
                    bullet_sell_tax,
                )
                n_trades += int(traded)
        curve[i] = core_shares * core[i] + bullet_shares * bullet[i] + cash
    return compute_metrics(pd.Series(curve, index=index), n_trades, 0, 0, initial_capital, risk_free_annual)


def backtest_buy_hold(
    bundle: SeriesBundle,
    initial_capital: float,
    risk_free_annual: float,
) -> BTResult:
    core = bundle.core.to_numpy()
    buy_value = initial_capital / (1.0 + COMMISSION)
    shares = buy_value / core[0]
    curve = pd.Series(shares * core, index=bundle.core.index)
    return compute_metrics(curve, 1, 0, 0, initial_capital, risk_free_annual)


def backtest_dca(
    bundle: SeriesBundle,
    initial_capital: float,
    risk_free_annual: float,
) -> BTResult:
    core = bundle.core.to_numpy()
    cash = initial_capital
    shares = 0.0
    monthly_purchase = initial_capital / (len(core) / 21.0)
    daily_rf = (1.0 + risk_free_annual) ** (1.0 / 252.0) - 1.0
    curve = np.empty(len(core), dtype=float)
    n_trades = 0
    for i, price in enumerate(core):
        if i > 0:
            cash *= 1.0 + daily_rf
        if i % 21 == 0 and cash > 0:
            spend = min(monthly_purchase, cash)
            buy_value = spend / (1.0 + COMMISSION)
            shares += buy_value / price
            cash -= spend
            n_trades += 1
        curve[i] = shares * price + cash
    return compute_metrics(pd.Series(curve, index=bundle.core.index), n_trades, 0, 0, initial_capital, risk_free_annual)


def slice_bundle(bundle: SeriesBundle, start: int, end: int | None = None) -> SeriesBundle:
    return SeriesBundle(
        taiex=bundle.taiex.iloc[start:end],
        core=bundle.core.iloc[start:end],
        bullet=bundle.bullet.iloc[start:end],
        signal=bundle.signal.iloc[start:end],
        core_type=bundle.core_type,
        bullet_type=bundle.bullet_type,
        source=bundle.source,
    )


def run_grid(
    bundle: SeriesBundle,
    core_pcts: list[float],
    risk_free_annual: float,
    midpoint_reset: bool,
    track_cooldown_extremes: bool,
) -> list[ConfigResult]:
    results: list[ConfigResult] = []
    total = len(FIRE_CONFIGS) * len(RETRIEVE_CONFIGS) * len(COOLDOWN_DAYS) * len(LOOKBACKS) * len(core_pcts)
    done = 0
    for core_pct in core_pcts:
        for fire_name, fire_thresholds in FIRE_CONFIGS.items():
            for retrieve_name, retrieve_thresholds in RETRIEVE_CONFIGS.items():
                for cooldown in COOLDOWN_DAYS:
                    for lookback in LOOKBACKS:
                        result = backtest_tactical(
                            bundle,
                            fire_thresholds,
                            retrieve_thresholds,
                            cooldown,
                            lookback,
                            core_pct,
                            INITIAL_CAPITAL,
                            risk_free_annual,
                            midpoint_reset,
                            track_cooldown_extremes,
                        )
                        results.append(ConfigResult(fire_name, retrieve_name, cooldown, lookback, core_pct, result))
                        done += 1
                        if done % 200 == 0:
                            print(f"  grid {done}/{total}", flush=True)
    results.sort(key=lambda cfg: (cfg.result.sharpe_excess, cfg.result.sortino_excess), reverse=True)
    return results


def run_config(
    bundle: SeriesBundle,
    cfg: ConfigResult,
    risk_free_annual: float,
    midpoint_reset: bool,
    track_cooldown_extremes: bool,
) -> BTResult:
    return backtest_tactical(
        bundle,
        FIRE_CONFIGS[cfg.fire_name],
        RETRIEVE_CONFIGS[cfg.retrieve_name],
        cfg.cooldown,
        cfg.lookback,
        cfg.core_pct,
        INITIAL_CAPITAL,
        risk_free_annual,
        midpoint_reset,
        track_cooldown_extremes,
    )


def benchmarks(bundle: SeriesBundle, core_pcts: list[float], risk_free_annual: float) -> dict[str, BTResult]:
    out = {
        "DCA monthly into core": backtest_dca(bundle, INITIAL_CAPITAL, risk_free_annual),
        "Buy & Hold 100% core": backtest_buy_hold(bundle, INITIAL_CAPITAL, risk_free_annual),
    }
    for core_pct in core_pcts:
        out[f"Static {int(core_pct * 100)}/{100 - int(core_pct * 100)} weekly"] = backtest_static(
            bundle,
            core_pct,
            INITIAL_CAPITAL,
            risk_free_annual,
        )
    return out


def format_result(name: str, result: BTResult) -> str:
    return (
        f"{name:<62} ret={result.total_return_pct:>7.2f}% "
        f"ann={result.annual_return_pct:>7.2f}% "
        f"sharpe={result.sharpe_excess:>6.2f} "
        f"sortino={result.sortino_excess:>6.2f} "
        f"maxDD={result.max_dd_pct:>7.2f}% "
        f"trades={result.n_trades:>3}"
    )


def write_grid_csv(configs: list[ConfigResult], output_path: Path) -> None:
    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "core_pct",
            "fire_config",
            "retrieve_config",
            "cooldown",
            "lookback",
            "total_return_pct",
            "annual_return_pct",
            "sharpe_excess",
            "sortino_excess",
            "max_dd_pct",
            "n_trades",
            "n_fire",
            "n_retrieve",
            "final_value",
        ])
        for cfg in configs:
            r = cfg.result
            writer.writerow([
                f"{cfg.core_pct:.2f}",
                cfg.fire_name,
                cfg.retrieve_name,
                cfg.cooldown,
                cfg.lookback,
                f"{r.total_return_pct:.4f}",
                f"{r.annual_return_pct:.4f}",
                f"{r.sharpe_excess:.6f}",
                f"{r.sortino_excess:.6f}",
                f"{r.max_dd_pct:.4f}",
                r.n_trades,
                r.n_fire,
                r.n_retrieve,
                f"{r.final_value:.2f}",
            ])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--core-ticker", default="0050")
    parser.add_argument("--bullet-ticker", default="00865B")
    parser.add_argument("--years", type=float, default=2.0)
    parser.add_argument("--risk-free", type=float, default=0.01, help="Annual risk-free rate, e.g. 0.01")
    parser.add_argument("--train-frac", type=float, default=0.60)
    parser.add_argument("--core-pcts", default=",".join(str(x) for x in CORE_PCTS))
    parser.add_argument("--signal-source", choices=["taiex", "core"], default="taiex")
    parser.add_argument("--no-midpoint-reset", action="store_true")
    parser.add_argument(
        "--cooldown-mode",
        choices=["track", "rigid"],
        default="track",
        help="track keeps the strongest signal seen during cooldown; rigid matches the original behavior.",
    )
    args = parser.parse_args()

    core_pcts = [float(x.strip()) for x in args.core_pcts.split(",") if x.strip()]
    core_pcts = [x if x <= 1 else x / 100.0 for x in core_pcts]
    core_pcts = sorted(set(core_pcts))

    bundle = load_bundle(args.core_ticker, args.bullet_ticker, args.years, args.signal_source)
    midpoint_reset = not args.no_midpoint_reset
    track_cooldown_extremes = args.cooldown_mode == "track"
    reset_slug = "reset" if midpoint_reset else "noreset"
    cooldown_slug = "cdtrack" if track_cooldown_extremes else "cdrigid"
    years_slug = f"{args.years:g}y".replace(".", "p")
    output_slug = (
        f"{args.core_ticker}_{args.bullet_ticker}_{years_slug}_{args.signal_source}_{reset_slug}_{cooldown_slug}"
        .replace("^", "IDX")
        .replace("/", "_")
    )
    results_csv = SCRIPT_DIR / f"codex_tactical_grid_train_{output_slug}.csv"
    summary_txt = SCRIPT_DIR / f"codex_audit_summary_{output_slug}.txt"
    split = int(len(bundle.taiex) * args.train_frac)
    train = slice_bundle(bundle, 0, split)
    holdout = slice_bundle(bundle, split, None)
    if len(holdout.taiex) <= max(LOOKBACKS) + 10:
        raise ValueError(f"Holdout too short: {len(holdout.taiex)} rows")

    print(f"Data: {bundle.taiex.index[0].date()} -> {bundle.taiex.index[-1].date()} ({len(bundle.taiex)} days)")
    print(f"Train: {train.taiex.index[0].date()} -> {train.taiex.index[-1].date()} ({len(train.taiex)} days)")
    print(f"Holdout: {holdout.taiex.index[0].date()} -> {holdout.taiex.index[-1].date()} ({len(holdout.taiex)} days)")
    print(f"Risk-free annual rate: {args.risk_free:.2%}")
    print(f"Price source: {bundle.source}")
    print(f"Signal source: {args.signal_source}")
    print(f"Midpoint reset: {'on' if midpoint_reset else 'off'}")
    print(f"Cooldown mode: {args.cooldown_mode}")

    train_grid = run_grid(train, core_pcts, args.risk_free, midpoint_reset, track_cooldown_extremes)
    write_grid_csv(train_grid, results_csv)
    chosen = train_grid[0]
    holdout_result = run_config(holdout, chosen, args.risk_free, midpoint_reset, track_cooldown_extremes)
    holdout_benchmarks = benchmarks(holdout, core_pcts, args.risk_free)

    lines: list[str] = []
    lines.append("Codex tactical audit")
    lines.append("====================")
    lines.append("")
    lines.append(f"Data: {bundle.taiex.index[0].date()} -> {bundle.taiex.index[-1].date()} ({len(bundle.taiex)} trading days)")
    lines.append(f"Train: {train.taiex.index[0].date()} -> {train.taiex.index[-1].date()} ({len(train.taiex)} trading days)")
    lines.append(f"Holdout: {holdout.taiex.index[0].date()} -> {holdout.taiex.index[-1].date()} ({len(holdout.taiex)} trading days)")
    lines.append(f"Risk-free annual rate: {args.risk_free:.2%}")
    lines.append(f"Price source: {bundle.source}")
    lines.append(f"Signal source: {args.signal_source}")
    lines.append(f"Midpoint reset: {'on' if midpoint_reset else 'off'}")
    lines.append(f"Cooldown mode: {args.cooldown_mode}")
    lines.append("")
    lines.append("Top train config")
    lines.append("----------------")
    lines.append(chosen.key())
    lines.append(format_result("Train tactical", chosen.result))
    lines.append("")
    lines.append("Holdout challenge")
    lines.append("-----------------")
    lines.append(format_result("Chosen tactical on holdout", holdout_result))
    for name, result in holdout_benchmarks.items():
        lines.append(format_result(name, result))
    lines.append("")
    best_benchmark = max(holdout_benchmarks.items(), key=lambda item: item[1].sharpe_excess)
    verdict = "FAIL" if holdout_result.sharpe_excess <= best_benchmark[1].sharpe_excess else "PASS"
    lines.append(f"Verdict: {verdict} vs best holdout benchmark by excess Sharpe ({best_benchmark[0]}).")
    lines.append("")
    lines.append("Important fixes vs original")
    lines.append("---------------------------")
    lines.append("- Signal uses today's close, execution happens next trading day.")
    lines.append("- Initial transaction costs reduce performance vs starting cash.")
    lines.append("- Rebalance costs are charged on the traded buy/sell legs.")
    lines.append("- Sharpe and Sortino use excess returns over the configured risk-free rate.")
    lines.append("- The selected config is challenged on a later holdout segment.")
    lines.append("- Cooldowns track the strongest signal seen during the pause.")

    summary_txt.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print()
    print("\n".join(lines))
    print(f"\nWrote {results_csv.relative_to(ROOT_DIR)}")
    print(f"Wrote {summary_txt.relative_to(ROOT_DIR)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
