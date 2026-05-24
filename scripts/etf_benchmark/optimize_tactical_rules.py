"""One-shot tactical-rule optimizer.

Run ONCE locally. Picks the best (FIRE percentiles, RETRIEVE percentiles,
cooldown, lookback window) combo for a 70%-core / 30%-bullet portfolio rule
system, by backtesting against TAIEX history.

The winner gets hard-coded into the 市場脈動 tab. No live tuning.

Strategy model
──────────────
    State:   deployment_pct ∈ [40%, 100%], starts at 70%
    Each day:
        rank = TAIEX percentile in last N trading days (0=lowest, 100=highest)
        target_fire     = # of FIRE thresholds the rank is below
        target_retrieve = # of RETRIEVE thresholds the rank is above
        If FIRE level wants to advance (and not in cooldown):
            engage new fire tiers, clear retrieve tiers,
            bump deployment_pct +6%/tier (capped at 100%)
        Same logic for RETRIEVE (subtract 6%/tier, floor 40%)
        Rebalance core vs bullet to new deployment_pct (apply costs)

Costs
─────
    BUY:  0.1425% commission (core or bullet)
    SELL: 0.1425% commission + 0.1% TW tax on equity (core)
    Round-trip cost approximated at 0.30%/rebalance (FIRE)
                                   or 0.39%/rebalance (RETRIEVE)

Run:
    python -m scripts.etf_benchmark.optimize_tactical_rules
    python -m scripts.etf_benchmark.optimize_tactical_rules --core-ticker 0050 --bullet-ticker 00865B
"""
from __future__ import annotations

import argparse
import csv
import math
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

DB_PATH      = ROOT_DIR / "data" / "etf_bench" / "etf_bench.sqlite"
RESULTS_CSV  = ROOT_DIR / "data" / "etf_bench" / "tactical_backtest_results.csv"

# ── constants ───────────────────────────────────────────────────────────────
INITIAL_CAPITAL    = 1_000_000.0   # NTD
TARGET_CORE_PCT    = 0.70
N_TIERS            = 5
TIER_STEP_PCT      = (1.0 - TARGET_CORE_PCT) / N_TIERS   # = 6% per tier
DEPLOY_MIN         = 1.0 - N_TIERS * TIER_STEP_PCT       # 40%
DEPLOY_MAX         = TARGET_CORE_PCT + N_TIERS * TIER_STEP_PCT  # 100%

COST_FIRE          = 0.0030    # ≈ 0.1425% × 2 (no tax on bond sell)
COST_RETRIEVE      = 0.0039    # ≈ 0.1425% × 2 + 0.1% TW equity sell tax

# ── parameter grid ──────────────────────────────────────────────────────────
# Each FIRE config = 5 percentile thresholds (monotonic, deeper = lower).
# Each RETRIEVE config = 5 percentile thresholds (monotonic, higher = more extreme).
FIRE_CONFIGS = {
    "A1_aggressive":   [30, 20, 15, 10, 5],
    "A2_standard":     [25, 15, 10, 5,  2],
    "A3_conservative": [20, 12, 7,  3,  1],
    "A4_xconservative":[15, 8,  4,  2,  1],
    "A5_narrow":       [20, 16, 12, 8,  4],
    "A6_wide":         [35, 22, 14, 8,  3],
}
RETRIEVE_CONFIGS = {
    "R1_aggressive":   [70, 80, 85, 90, 95],
    "R2_standard":     [75, 85, 90, 95, 98],
    "R3_conservative": [80, 88, 93, 97, 99],
    "R4_xconservative":[85, 92, 96, 98, 99],
    "R5_narrow":       [80, 84, 88, 92, 96],
    "R6_wide":         [65, 78, 86, 92, 97],
}
COOLDOWN_DAYS = [3, 5, 7]
LOOKBACKS     = [60, 90]

# ── data loaders ────────────────────────────────────────────────────────────
def load_close(ticker: str, use_adj: bool = True) -> pd.Series:
    """Daily close (or adj_close) for one ticker from the SQLite, sorted by date."""
    col = "adj_close" if use_adj else "close"
    with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as conn:
        df = pd.read_sql_query(
            f"SELECT date, {col} AS price FROM prices "
            f"WHERE ticker = ? AND {col} IS NOT NULL "
            f"ORDER BY date",
            conn, params=[ticker],
        )
    if df.empty:
        raise ValueError(f"No prices for {ticker} in DB")
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")["price"]


def align_series(*series: pd.Series) -> tuple[pd.Series, ...]:
    """Inner-join all series on date so they line up day-by-day."""
    df = pd.concat(series, axis=1, join="inner").dropna()
    return tuple(df.iloc[:, i] for i in range(df.shape[1]))


# ── backtest ────────────────────────────────────────────────────────────────
@dataclass
class BTResult:
    total_return_pct: float
    annual_return_pct: float
    sharpe: float
    sortino: float
    max_dd_pct: float
    n_trades: int
    n_fire: int
    n_retrieve: int
    final_value: float
    portfolio_curve: pd.Series


def percentile_rank(window: np.ndarray, current: float) -> float:
    """Where does `current` fall in the sorted window? 0 = lowest, 100 = highest."""
    return float((window <= current).sum()) / len(window) * 100.0


def backtest_tactical(
    taiex:             pd.Series,
    core_prices:       pd.Series,
    bullet_prices:     pd.Series,
    fire_thresholds:   list[int],
    retrieve_thresholds: list[int],
    cooldown_days:     int,
    lookback_days:     int,
    target_core_pct:   float = TARGET_CORE_PCT,
    initial_capital:   float = INITIAL_CAPITAL,
) -> BTResult:
    """Run one rule-config backtest. Returns metrics + daily portfolio curve."""
    n = len(taiex)
    taiex_arr = taiex.to_numpy()
    core_arr  = core_prices.to_numpy()
    bul_arr   = bullet_prices.to_numpy()

    # Initial allocation at target_core_pct / (1 - target_core_pct)
    core_shares  = (initial_capital * target_core_pct)       / core_arr[0]
    bullet_shares = (initial_capital * (1 - target_core_pct)) / bul_arr[0]

    fire_active     = 0
    retrieve_active = 0
    fire_cd_until     = -1
    retrieve_cd_until = -1
    last_deployment = target_core_pct

    portfolio_curve = np.empty(n, dtype=float)
    n_trades = n_fire = n_retrieve = 0

    for i in range(n):
        p_core = core_arr[i]
        p_bul  = bul_arr[i]

        # Hold flat until we have a full lookback window
        if i < lookback_days:
            portfolio_curve[i] = core_shares * p_core + bullet_shares * p_bul
            continue

        window = taiex_arr[i - lookback_days : i + 1]
        rank = percentile_rank(window, taiex_arr[i])

        # Target tier levels driven by current rank
        target_fire     = sum(1 for t in fire_thresholds     if rank <= t)
        target_retrieve = sum(1 for t in retrieve_thresholds if rank >= t)

        # FIRE — advance counter if rank dropped further
        if target_fire > fire_active and i > fire_cd_until:
            fire_active     = target_fire
            retrieve_active = 0
            fire_cd_until   = i + cooldown_days
            n_fire += 1
        # RETRIEVE — advance counter if rank rose further
        if target_retrieve > retrieve_active and i > retrieve_cd_until:
            retrieve_active = target_retrieve
            fire_active     = 0
            retrieve_cd_until = i + cooldown_days
            n_retrieve += 1
        # Natural decay: if rank returns to the middle, reset tiers
        # (only when not in cooldown — lets the next move re-engage cleanly)
        if i > max(fire_cd_until, retrieve_cd_until):
            mid_pct = 50
            if fire_active > 0 and rank > mid_pct:
                fire_active = 0
            if retrieve_active > 0 and rank < mid_pct:
                retrieve_active = 0

        # Compute target deployment %
        deployment_pct = target_core_pct + TIER_STEP_PCT * (fire_active - retrieve_active)
        deployment_pct = max(DEPLOY_MIN, min(DEPLOY_MAX, deployment_pct))

        # Rebalance only when deployment changes
        if abs(deployment_pct - last_deployment) > 1e-6:
            total = core_shares * p_core + bullet_shares * p_bul
            target_core_val   = total * deployment_pct
            target_bullet_val = total * (1 - deployment_pct)
            current_core_val  = core_shares * p_core
            delta = target_core_val - current_core_val

            cost_rate = COST_FIRE if delta > 0 else COST_RETRIEVE
            cost = abs(delta) * cost_rate

            core_shares   = target_core_val   / p_core
            bullet_shares = (target_bullet_val - cost) / p_bul
            last_deployment = deployment_pct
            n_trades += 1

        portfolio_curve[i] = core_shares * p_core + bullet_shares * p_bul

    pv = pd.Series(portfolio_curve, index=taiex.index)
    return _compute_metrics(pv, n_trades, n_fire, n_retrieve)


def backtest_dca(core_prices: pd.Series,
                 initial_capital: float = INITIAL_CAPITAL) -> BTResult:
    """Equal monthly purchases of core, full 100% target."""
    # Approximate "monthly" as every 21 trading days
    n = len(core_prices)
    monthly_purchase = initial_capital / (n / 21)
    arr = core_prices.to_numpy()
    cash_remaining = initial_capital
    shares = 0.0
    n_trades = 0
    pv = np.empty(n)

    for i in range(n):
        if i % 21 == 0 and cash_remaining > 0:
            buy = min(monthly_purchase, cash_remaining)
            cost = buy * 0.001425
            shares += (buy - cost) / arr[i]
            cash_remaining -= buy
            n_trades += 1
        pv[i] = shares * arr[i] + cash_remaining
    return _compute_metrics(pd.Series(pv, index=core_prices.index),
                            n_trades, 0, 0)


def backtest_static_split(core_prices: pd.Series,
                          bullet_prices: pd.Series,
                          target_core_pct: float = TARGET_CORE_PCT,
                          initial_capital: float = INITIAL_CAPITAL,
                          rebalance_every_n_days: int = 5) -> BTResult:
    """70/30 with weekly rebalance, no tactical signals."""
    n = len(core_prices)
    core_arr = core_prices.to_numpy()
    bul_arr  = bullet_prices.to_numpy()

    core_shares   = (initial_capital * target_core_pct)       / core_arr[0]
    bullet_shares = (initial_capital * (1 - target_core_pct)) / bul_arr[0]
    n_trades = 0
    pv = np.empty(n)

    for i in range(n):
        if i > 0 and i % rebalance_every_n_days == 0:
            total = core_shares * core_arr[i] + bullet_shares * bul_arr[i]
            target_core_val = total * target_core_pct
            delta = target_core_val - core_shares * core_arr[i]
            if abs(delta) > total * 0.005:
                cost_rate = COST_FIRE if delta > 0 else COST_RETRIEVE
                cost = abs(delta) * cost_rate
                core_shares = target_core_val / core_arr[i]
                bullet_shares = (total - target_core_val - cost) / bul_arr[i]
                n_trades += 1
        pv[i] = core_shares * core_arr[i] + bullet_shares * bul_arr[i]
    return _compute_metrics(pd.Series(pv, index=core_prices.index),
                            n_trades, 0, 0)


def backtest_buy_hold(core_prices: pd.Series,
                      initial_capital: float = INITIAL_CAPITAL) -> BTResult:
    arr = core_prices.to_numpy()
    shares = initial_capital * (1 - 0.001425) / arr[0]
    pv = shares * arr
    return _compute_metrics(pd.Series(pv, index=core_prices.index), 1, 0, 0)


# ── metrics ─────────────────────────────────────────────────────────────────
def _compute_metrics(pv: pd.Series, n_trades: int,
                     n_fire: int, n_retrieve: int) -> BTResult:
    initial = float(pv.iloc[0])
    final   = float(pv.iloc[-1])
    total_return = (final / initial - 1.0) * 100.0
    n_days = len(pv)
    annual_return = ((final / initial) ** (252.0 / max(n_days, 1)) - 1.0) * 100.0

    rets = pv.pct_change().dropna()
    if len(rets) > 1 and rets.std() > 0:
        sharpe = (rets.mean() / rets.std()) * math.sqrt(252)
    else:
        sharpe = 0.0
    down_rets = rets[rets < 0]
    if len(down_rets) > 1 and down_rets.std() > 0:
        sortino = (rets.mean() / down_rets.std()) * math.sqrt(252)
    else:
        sortino = 0.0
    peak = pv.cummax()
    dd = (pv - peak) / peak
    max_dd = float(dd.min()) * 100.0

    return BTResult(
        total_return_pct=total_return,
        annual_return_pct=annual_return,
        sharpe=sharpe,
        sortino=sortino,
        max_dd_pct=max_dd,
        n_trades=n_trades,
        n_fire=n_fire,
        n_retrieve=n_retrieve,
        final_value=final,
        portfolio_curve=pv,
    )


# ── grid search ─────────────────────────────────────────────────────────────
@dataclass
class ConfigResult:
    fire_name: str
    retrieve_name: str
    cooldown: int
    lookback: int
    result: BTResult

    def key(self) -> str:
        return f"{self.fire_name}/{self.retrieve_name}/cd={self.cooldown}/win={self.lookback}"


def run_grid(taiex: pd.Series,
             core_prices: pd.Series,
             bullet_prices: pd.Series) -> list[ConfigResult]:
    out: list[ConfigResult] = []
    total = len(FIRE_CONFIGS) * len(RETRIEVE_CONFIGS) * len(COOLDOWN_DAYS) * len(LOOKBACKS)
    done = 0
    for fname, ft in FIRE_CONFIGS.items():
        for rname, rt in RETRIEVE_CONFIGS.items():
            for cd in COOLDOWN_DAYS:
                for lb in LOOKBACKS:
                    bt = backtest_tactical(
                        taiex, core_prices, bullet_prices,
                        fire_thresholds=ft, retrieve_thresholds=rt,
                        cooldown_days=cd, lookback_days=lb,
                    )
                    out.append(ConfigResult(fname, rname, cd, lb, bt))
                    done += 1
                    if done % 50 == 0:
                        print(f"  [grid] {done}/{total} configs done", flush=True)
    return out


# ── robustness check ────────────────────────────────────────────────────────
def robustness_score(cfg: ConfigResult, all_configs: list[ConfigResult]) -> float:
    """Average Sharpe of "neighbour" configs: same FIRE/RETRIEVE family,
    ±1 step in cooldown and lookback. High = config is in a stable plateau,
    not a lucky spike. Returns mean Sharpe of up to 4 neighbours."""
    neighbours: list[float] = []
    for other in all_configs:
        if other is cfg:
            continue
        if other.fire_name != cfg.fire_name or other.retrieve_name != cfg.retrieve_name:
            continue
        cd_step = abs(COOLDOWN_DAYS.index(other.cooldown) - COOLDOWN_DAYS.index(cfg.cooldown))
        lb_step = abs(LOOKBACKS.index(other.lookback)     - LOOKBACKS.index(cfg.lookback))
        if cd_step + lb_step <= 1:
            neighbours.append(other.result.sharpe)
    return float(np.mean(neighbours)) if neighbours else cfg.result.sharpe


# ── output ──────────────────────────────────────────────────────────────────
def print_header(title: str) -> None:
    print()
    print("═" * 75)
    print(f"  {title}")
    print("═" * 75)


def print_top_n(configs: list[ConfigResult], n: int = 10) -> None:
    print(f"{'Rank':<5}{'Config':<48}{'Ret%':>7}{'Sharpe':>8}{'Sortino':>9}"
          f"{'MaxDD%':>9}{'#Trd':>6}")
    print("─" * 92)
    for i, c in enumerate(configs[:n], start=1):
        r = c.result
        print(f"{i:<5}{c.key():<48}{r.total_return_pct:>7.1f}"
              f"{r.sharpe:>8.2f}{r.sortino:>9.2f}"
              f"{r.max_dd_pct:>9.1f}{r.n_trades:>6}")


def print_benchmarks(bms: dict[str, BTResult]) -> None:
    print(f"{'':<5}{'Benchmark':<48}{'Ret%':>7}{'Sharpe':>8}{'Sortino':>9}"
          f"{'MaxDD%':>9}{'#Trd':>6}")
    print("─" * 92)
    for name, r in bms.items():
        print(f"{'':<5}{name:<48}{r.total_return_pct:>7.1f}"
              f"{r.sharpe:>8.2f}{r.sortino:>9.2f}"
              f"{r.max_dd_pct:>9.1f}{r.n_trades:>6}")


def print_recommendation(top: ConfigResult, robust_score: float,
                         benchmarks: dict[str, BTResult]) -> None:
    static = benchmarks.get("Static 70/30 (weekly rebalance)")
    dca    = benchmarks.get("DCA monthly into core")
    bh     = benchmarks.get("Buy & Hold 100% core")

    beats_static = static is None or top.result.sharpe > static.sharpe
    beats_dca    = dca    is None or top.result.sharpe > dca.sharpe
    confidence = ("HIGH" if (robust_score >= top.result.sharpe * 0.93
                              and beats_static and beats_dca)
                  else "MEDIUM" if beats_static or beats_dca
                  else "LOW")

    fire_ths     = FIRE_CONFIGS[top.fire_name]
    retrieve_ths = RETRIEVE_CONFIGS[top.retrieve_name]
    print(f"  WINNER: {top.key()}")
    print(f"    FIRE percentiles:     {' / '.join(str(t) for t in fire_ths)}")
    print(f"    RETRIEVE percentiles: {' / '.join(str(t) for t in retrieve_ths)}")
    print(f"    Cooldown:             {top.cooldown} trading days")
    print(f"    Lookback window:      {top.lookback} trading days")
    print()
    print(f"    Return:          {top.result.total_return_pct:+.1f}% "
          f"(annualised {top.result.annual_return_pct:+.1f}%)")
    print(f"    Sharpe:          {top.result.sharpe:.2f}")
    print(f"    Sortino:         {top.result.sortino:.2f}")
    print(f"    Max drawdown:    {top.result.max_dd_pct:.1f}%")
    print(f"    Trades:          {top.result.n_trades}  "
          f"(FIRE {top.result.n_fire}, RETRIEVE {top.result.n_retrieve})")
    print(f"    Robustness:      neighbour Sharpe = {robust_score:.2f}")
    print()
    print(f"  Confidence: {confidence}")
    if not beats_static:
        print("    ⚠  Tactical layer does NOT beat static 70/30 — strategy adds no alpha.")
    if not beats_dca:
        print("    ⚠  Tactical layer does NOT beat plain DCA — strategy adds no alpha.")


def write_results_csv(configs: list[ConfigResult]) -> None:
    RESULTS_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULTS_CSV, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([
            "fire_config", "retrieve_config", "cooldown", "lookback",
            "total_return_pct", "annual_return_pct", "sharpe", "sortino",
            "max_dd_pct", "n_trades", "n_fire", "n_retrieve", "final_value",
        ])
        for c in configs:
            r = c.result
            w.writerow([
                c.fire_name, c.retrieve_name, c.cooldown, c.lookback,
                f"{r.total_return_pct:.3f}", f"{r.annual_return_pct:.3f}",
                f"{r.sharpe:.4f}", f"{r.sortino:.4f}", f"{r.max_dd_pct:.3f}",
                r.n_trades, r.n_fire, r.n_retrieve, f"{r.final_value:.2f}",
            ])
    print(f"  Full results saved to: {RESULTS_CSV.relative_to(ROOT_DIR)}")


# ── main ────────────────────────────────────────────────────────────────────
def main() -> int:
    # Force UTF-8 on Windows console so ─ ═ ✓ ⚠ render correctly
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--core-ticker",   default="0050",
                    help="Always-invested core ETF (default 0050)")
    ap.add_argument("--bullet-ticker", default="00865B",
                    help="Defensive bullet ETF (default 00865B US short bonds)")
    ap.add_argument("--core-pct", type=float, default=TARGET_CORE_PCT,
                    help=f"Baseline core % (default {TARGET_CORE_PCT})")
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"DB not found at {DB_PATH}. Run step2 + step3 first.")
        return 1

    print(f"[backtest] Core:   {args.core_ticker}")
    print(f"[backtest] Bullet: {args.bullet_ticker}")
    print(f"[backtest] Loading TAIEX + price series …")
    taiex   = load_close("^TWII",            use_adj=False)
    core    = load_close(args.core_ticker,   use_adj=True)
    bullet  = load_close(args.bullet_ticker, use_adj=True)
    taiex, core, bullet = align_series(taiex, core, bullet)
    print(f"[backtest] Aligned window: {taiex.index[0].date()} → {taiex.index[-1].date()} "
          f"({len(taiex)} trading days)")

    total = len(FIRE_CONFIGS) * len(RETRIEVE_CONFIGS) * len(COOLDOWN_DAYS) * len(LOOKBACKS)
    print(f"[backtest] Running {total} tactical configurations …")
    all_configs = run_grid(taiex, core, bullet)

    # Sort by Sharpe (primary), Sortino (tiebreak)
    all_configs.sort(key=lambda c: (c.result.sharpe, c.result.sortino), reverse=True)

    print_header("BACKTEST RESULTS — top 10 by Sharpe ratio")
    print_top_n(all_configs, n=10)

    # Benchmarks
    print_header("BENCHMARKS")
    benchmarks = {
        "DCA monthly into core":             backtest_dca(core),
        "Static 70/30 (weekly rebalance)":   backtest_static_split(core, bullet,
                                                                   target_core_pct=args.core_pct),
        "Buy & Hold 100% core":              backtest_buy_hold(core),
    }
    print_benchmarks(benchmarks)

    # Robustness check on top 3
    print_header("ROBUSTNESS CHECK — top 3 configurations")
    print(f"{'Rank':<5}{'Config':<48}{'Sharpe':>8}{'Neighbour avg':>15}{'Flag':>8}")
    print("─" * 84)
    top_with_robust: list[tuple[ConfigResult, float]] = []
    for i, c in enumerate(all_configs[:3], start=1):
        rs = robustness_score(c, all_configs)
        ratio = rs / c.result.sharpe if c.result.sharpe > 0 else 0
        flag = "✓" if ratio >= 0.93 else "⚠"
        print(f"{i:<5}{c.key():<48}{c.result.sharpe:>8.2f}{rs:>15.2f}{flag:>8}")
        top_with_robust.append((c, rs))

    # Pick the most robust of the top 3 (not just rank #1)
    winner, winner_robust = max(top_with_robust,
                                key=lambda pair: (pair[1], pair[0].result.sharpe))

    print_header("RECOMMENDATION")
    print_recommendation(winner, winner_robust, benchmarks)
    print()
    print("  ⚠ Caveats")
    print(f"    • Backtest window: only {len(taiex)} trading days. Contains ~1 real bear.")
    print("    • RETRIEVE thresholds are undertrained — re-run after more bears.")
    print("    • Costs approximated (commission + tax, no slippage / no bid-ask spread).")
    print("    • Past ≠ future. World-changing events not in this data.")
    print()
    write_results_csv(all_configs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
