from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf


OUT_DIR = Path(__file__).resolve().parent
CACHE_DIR = OUT_DIR / ".yfinance_cache"
TRADING_DAYS_3M = 63


@dataclass(frozen=True)
class EventConfig:
    name: str
    min_z: float
    min_ret30: float
    min_ret60: float
    min_dist_high: float
    cooldown_days: int = 63


EVENT_CONFIGS = [
    EventConfig("similar_now_strict", min_z=1.8, min_ret30=15.0, min_ret60=15.0, min_dist_high=-1.0),
    EventConfig("hot_near_high", min_z=1.5, min_ret30=10.0, min_ret60=12.0, min_dist_high=-1.5),
    EventConfig("stretch_only", min_z=1.8, min_ret30=5.0, min_ret60=8.0, min_dist_high=-2.0),
    # Broader "hot but not necessarily extreme" samples. These are designed
    # to answer the allocation question with enough historical events instead
    # of overfitting to only the most dramatic overheating episodes.
    EventConfig("warm_near_high", min_z=1.0, min_ret30=6.0, min_ret60=8.0, min_dist_high=-3.0, cooldown_days=42),
    EventConfig("momentum_near_high", min_z=0.8, min_ret30=8.0, min_ret60=10.0, min_dist_high=-3.0, cooldown_days=42),
    EventConfig("near_high_positive_momentum", min_z=0.5, min_ret30=4.0, min_ret60=6.0, min_dist_high=-2.0, cooldown_days=42),
]


def fetch(ticker: str, start: str = "2003-01-01") -> pd.DataFrame:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / f"{ticker.replace('^', 'IDX_').replace('.', '_')}_{start}.csv"
    if cache.exists():
        df = pd.read_csv(cache, index_col=0, parse_dates=True)
        df.index.name = "Date"
        return df
    df = yf.download(ticker, start=start, auto_adjust=False, progress=False, threads=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    if df.empty:
        raise RuntimeError(f"No data for {ticker}")
    df.index = pd.to_datetime(df.index).tz_localize(None)
    df.to_csv(cache)
    return df


def adjusted_close(df: pd.DataFrame) -> pd.Series:
    col = "Adj Close" if "Adj Close" in df.columns else "Close"
    return pd.to_numeric(df[col], errors="coerce").dropna()


def trailing_zscore_stretch(prices: pd.Series, ma_window: int = 200, lookback: int = 504) -> pd.DataFrame:
    ma = prices.rolling(ma_window).mean()
    stretch = (prices / ma - 1.0) * 100.0
    mean = stretch.shift(1).rolling(lookback, min_periods=252).mean()
    std = stretch.shift(1).rolling(lookback, min_periods=252).std()
    z = (stretch - mean) / std
    return pd.DataFrame({"stretch": stretch, "z": z})


def find_events(taiex: pd.Series, cfg: EventConfig) -> pd.DataFrame:
    metrics = trailing_zscore_stretch(taiex)
    df = pd.DataFrame(index=taiex.index)
    df["close"] = taiex
    df["z"] = metrics["z"]
    df["stretch"] = metrics["stretch"]
    df["ret30"] = taiex.pct_change(30) * 100.0
    df["ret60"] = taiex.pct_change(60) * 100.0
    high252 = taiex.rolling(252, min_periods=126).max()
    df["dist_high"] = (taiex / high252 - 1.0) * 100.0
    mask = (
        (df["z"] >= cfg.min_z)
        & (df["ret30"] >= cfg.min_ret30)
        & (df["ret60"] >= cfg.min_ret60)
        & (df["dist_high"] >= cfg.min_dist_high)
    )
    candidates = df.loc[mask].dropna()

    events = []
    last_idx = -10**9
    positions = {date: i for i, date in enumerate(df.index)}
    for date, row in candidates.iterrows():
        pos = positions[date]
        if pos - last_idx < cfg.cooldown_days:
            continue
        events.append((date, row))
        last_idx = pos
    if not events:
        return pd.DataFrame(columns=["date", "close", "z", "stretch", "ret30", "ret60", "dist_high"])
    out = pd.DataFrame([r for _, r in events])
    out.insert(0, "date", [d for d, _ in events])
    return out.reset_index(drop=True)


def align_price_for_dates(asset: pd.Series, event_dates: pd.Series) -> dict[pd.Timestamp, int]:
    idx = asset.index
    positions: dict[pd.Timestamp, int] = {}
    for event_date in event_dates:
        loc = idx.searchsorted(pd.Timestamp(event_date))
        if loc < len(idx):
            positions[pd.Timestamp(event_date)] = int(loc)
    return positions


def simulate_plan(asset: pd.Series, start_pos: int, horizon: int, plan: str) -> dict[str, float]:
    end_pos = start_pos + horizon
    if end_pos >= len(asset):
        raise IndexError("not enough future data")
    px = asset.iloc[start_pos : end_pos + 1].to_numpy(dtype=float)
    ratios = px / px[0]

    deployed = np.zeros(len(px), dtype=float)
    if plan == "immediate_40":
        deployed[:] = 0.40
    elif plan == "cash_wait":
        deployed[:] = 0.0
    elif plan == "time_10_monthly":
        for day, amount in [(0, 0.10), (21, 0.10), (42, 0.10), (63, 0.10)]:
            deployed[day:] += amount
    elif plan == "time_5_weekly":
        for day in range(0, 56, 7):
            deployed[day:] += 0.05
    elif plan == "dip_3_6_10_time":
        # 20% price bullets, 20% time bullets.
        deployed[0:] += 0.07
        deployed[min(21, horizon):] += 0.07
        deployed[min(42, horizon):] += 0.06
        drawdown = ratios - np.maximum.accumulate(ratios)
        fired = 0.0
        for threshold in [-0.03, -0.06, -0.10]:
            hits = np.where(drawdown <= threshold)[0]
            if len(hits):
                deployed[hits[0] :] += 0.10
                fired += 0.10
        # If no enough dips by the end, force undeployed price bullet at day 63.
        deployed[-1:] += max(0.0, 0.30 - fired)
    elif plan == "dip_5_10_15_or_end":
        drawdown = ratios - np.maximum.accumulate(ratios)
        fired = 0.0
        for threshold, amount in [(-0.05, 0.15), (-0.10, 0.15), (-0.15, 0.10)]:
            hits = np.where(drawdown <= threshold)[0]
            if len(hits):
                deployed[hits[0] :] += amount
                fired += amount
        deployed[-1:] += max(0.0, 0.40 - fired)
    elif plan == "wait_20d_then_all":
        deployed[min(20, horizon) :] = 0.40
    elif plan == "wait_40d_then_all":
        deployed[min(40, horizon) :] = 0.40
    else:
        raise ValueError(plan)

    deployed = np.minimum(deployed, 0.40)
    # Existing 60% buy-and-hold plus scheduled 40% bullet capital.
    units = 0.60 / px[0]
    cash = 0.40
    prev_deployed = 0.0
    equity_curve = []
    for i, price in enumerate(px):
        add = deployed[i] - prev_deployed
        if add > 1e-12:
            units += add / price
            cash -= add
            prev_deployed = deployed[i]
        equity_curve.append(units * price + cash)
    curve = np.array(equity_curve)
    total = curve[-1] / curve[0] - 1.0
    max_dd = (curve / np.maximum.accumulate(curve) - 1.0).min()
    fully_day = int(np.argmax(deployed >= 0.399999)) if np.any(deployed >= 0.399999) else -1
    return {
        "return_3m_pct": total * 100.0,
        "max_dd_3m_pct": max_dd * 100.0,
        "end_deployed_pct": deployed[-1] * 100.0,
        "fully_deployed_day": fully_day,
    }


def run(asset_ticker: str = "0050.TW", start: str = "2003-01-01") -> None:
    taiex = adjusted_close(fetch("^TWII", start=start))
    asset = adjusted_close(fetch(asset_ticker, start=start))
    common_start = max(taiex.index.min(), asset.index.min())
    taiex = taiex[taiex.index >= common_start]
    asset = asset[asset.index >= common_start]

    plans = [
        "immediate_40",
        "cash_wait",
        "time_10_monthly",
        "time_5_weekly",
        "dip_3_6_10_time",
        "dip_5_10_15_or_end",
        "wait_20d_then_all",
        "wait_40d_then_all",
    ]

    all_rows = []
    event_rows = []
    for cfg in EVENT_CONFIGS:
        events = find_events(taiex, cfg)
        pos_map = align_price_for_dates(asset, events["date"]) if not events.empty else {}
        valid_events = []
        for _, event in events.iterrows():
            date = pd.Timestamp(event["date"])
            if date not in pos_map:
                continue
            start_pos = pos_map[date]
            if start_pos + TRADING_DAYS_3M >= len(asset):
                continue
            valid_events.append(event)
            event_rows.append({"config": cfg.name, **event.to_dict()})
            for plan in plans:
                metrics = simulate_plan(asset, start_pos, TRADING_DAYS_3M, plan)
                all_rows.append({"config": cfg.name, "date": date.date().isoformat(), "plan": plan, **metrics})

        print(f"{cfg.name}: {len(valid_events)} events")
        if valid_events:
            print("  dates:", ", ".join(pd.Timestamp(e["date"]).date().isoformat() for e in valid_events[:12]))

    result = pd.DataFrame(all_rows)
    events_df = pd.DataFrame(event_rows)
    if result.empty:
        raise RuntimeError("No valid overheat events found")

    summary = (
        result.groupby(["config", "plan"], as_index=False)
        .agg(
            n=("return_3m_pct", "size"),
            avg_return_3m_pct=("return_3m_pct", "mean"),
            median_return_3m_pct=("return_3m_pct", "median"),
            win_rate=("return_3m_pct", lambda s: (s > 0).mean() * 100.0),
            avg_max_dd_3m_pct=("max_dd_3m_pct", "mean"),
            worst_max_dd_3m_pct=("max_dd_3m_pct", "min"),
            avg_fully_deployed_day=("fully_deployed_day", lambda s: s[s >= 0].mean()),
        )
        .sort_values(["config", "median_return_3m_pct"], ascending=[True, False])
    )

    result_path = OUT_DIR / f"overheat_bullet_results_{asset_ticker.replace('.', '_')}.csv"
    summary_path = OUT_DIR / f"overheat_bullet_summary_{asset_ticker.replace('.', '_')}.csv"
    events_path = OUT_DIR / f"overheat_events_{asset_ticker.replace('.', '_')}.csv"
    txt_path = OUT_DIR / f"overheat_bullet_takeaways_{asset_ticker.replace('.', '_')}.txt"
    result.to_csv(result_path, index=False, encoding="utf-8-sig")
    summary.to_csv(summary_path, index=False, encoding="utf-8-sig")
    events_df.to_csv(events_path, index=False, encoding="utf-8-sig")

    lines = [
        f"Overheat bullet experiment for {asset_ticker}",
        "=" * 48,
        "Start condition uses ^TWII trailing metrics; forward return uses the asset ETF.",
        "Portfolio starts 60% invested / 40% cash; each plan decides how to deploy the 40% over 63 trading days.",
        "",
    ]
    for cfg in EVENT_CONFIGS:
        sub = summary[summary["config"] == cfg.name]
        if sub.empty:
            continue
        lines.append(cfg.name)
        lines.append("-" * len(cfg.name))
        for _, row in sub.iterrows():
            lines.append(
                f"{row['plan']}: n={int(row['n'])}, median={row['median_return_3m_pct']:.2f}%, "
                f"avg={row['avg_return_3m_pct']:.2f}%, win={row['win_rate']:.0f}%, "
                f"avgDD={row['avg_max_dd_3m_pct']:.2f}%, worstDD={row['worst_max_dd_3m_pct']:.2f}%"
            )
        lines.append("")
    txt_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {summary_path}")
    print(f"Wrote {events_path}")
    print(f"Wrote {txt_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset", default="0050.TW", help="ETF used for forward bullet deployment, e.g. 0050.TW")
    parser.add_argument("--start", default="2003-01-01")
    args = parser.parse_args()
    run(args.asset, args.start)


if __name__ == "__main__":
    main()
