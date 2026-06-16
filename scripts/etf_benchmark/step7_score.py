"""Step 7 — record every ETF's fair-score pillars per day into a tracked CSV.

The ETF 比較 tab's composite is built from three direction-neutral pillars
(效率 / 不對稱 / 一致性). This step computes those pillars for *all* eligible ETFs
and appends one row per ETF per trading day, so the website can plot each fund's
score history and recombine the pillars live (custom weights + confidence).

Design choices that make a daily, all-ETF record well-defined:
  • Stable basis — each ETF is ranked WITHIN ITS ASSET CLASS (equity / bond /
    commodity / other), against all same-class ETFs with prices. No user
    selection, so the stored number is reproducible day to day.
  • Trailing window — metrics use the last ~1 year up to the as-of date (a young
    fund uses its full available history). This is each fund's "current" standing.
  • Per-fund gate — a fund is recorded only once it has ≥ MIN_DAYS of its own
    data, so a later-listed ETF's history starts 30 trading days after listing.
  • We store the pillar percentile sub-scores (0-100) + n_days, NOT the weighted
    composite — the composite/weights/confidence stay interactive in the website.

Persisted to data/etf_bench/score_history.csv (long format), tracked in git so it
survives DB rebuilds and syncs to local checkouts.

Run:
    python -m scripts.etf_benchmark.step7_score                      # append today only
    python -m scripts.etf_benchmark.step7_score --backfill           # last 1 year (default)
    python -m scripts.etf_benchmark.step7_score --backfill --years 2
    python -m scripts.etf_benchmark.step7_score --backfill --start 2026-02-23
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Quieten the "No runtime found" noise from the Streamlit-cached db helpers in CLI use.
for _n in ("streamlit", "streamlit.runtime.caching.cache_data_api",
           "streamlit.runtime.caching"):
    logging.getLogger(_n).setLevel(logging.ERROR)

from scripts.etf_benchmark import db                       # noqa: E402
from src.ui.etf_compare_tab import _build_score_table      # noqa: E402

# ── knobs ────────────────────────────────────────────────────────────────────
HIST_CSV       = ROOT_DIR / "data" / "etf_bench" / "score_history.csv"
DEFAULT_YEARS  = 1                           # backfill this many years of history by default
LOOKBACK       = pd.DateOffset(years=1)      # trailing window for the metrics
MIN_DAYS       = 30                          # ≥ this many of the fund's OWN trading days
WEIGHTS        = {"efficiency": 1.0, "asymmetry": 1.0, "consistency": 1.0}  # only pillars stored
REF_INDICES    = ("^TWII", "^IXIC", "^GSPC", "^DJI")  # benchmarks used by the asymmetry pillar

# fund_type → asset class the fund is ranked within
ASSET_CLASS = {
    "passive_equity": "equity",
    "active_equity":  "equity",
    "leveraged":      "equity",
    "bond":           "bond",
    "commodity":      "commodity",
    "other":          "other",
    # 'index' / reference rows are excluded below
}

PILLAR_COLS = {"效率": "eff", "不對稱": "asy", "一致性": "con"}


def _eligible(universe: pd.DataFrame) -> pd.DataFrame:
    u = universe[universe["has_prices"] & universe["market"].isin(["TWSE", "TPEx"])].copy()
    u["asset_class"] = u["fund_type"].map(ASSET_CLASS)
    return u[u["asset_class"].notna()]


def scores_as_of(universe: pd.DataFrame, eligible: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    """One row per recordable ETF for a single as-of date."""
    baseline = pd.Timestamp(as_of) - LOOKBACK
    rows: list[dict] = []
    for asset_class, grp in eligible.groupby("asset_class"):
        tickers = grp["ticker"].tolist()
        sdf = _build_score_table(tickers, universe, baseline, WEIGHTS,
                                 as_of=as_of, shrink=False)
        for tkr, r in sdf.iterrows():
            if int(r["n_days"]) < MIN_DAYS:        # per-fund gate
                continue
            row = {
                "date": pd.Timestamp(as_of).date().isoformat(),
                "ticker": tkr,
                "asset_class": asset_class,
                "n_days": int(r["n_days"]),
            }
            for zh, en in PILLAR_COLS.items():
                v = r.get(zh)
                row[en] = round(float(v), 2) if pd.notna(v) else None
            rows.append(row)
    return pd.DataFrame(rows)


def _upsert(df_new: pd.DataFrame) -> int:
    """Replace any existing rows for the dates in df_new, keep the rest."""
    HIST_CSV.parent.mkdir(parents=True, exist_ok=True)
    if HIST_CSV.exists():
        old = pd.read_csv(HIST_CSV)
        old["date"] = old["date"].astype(str)
        out = pd.concat([old[~old["date"].isin(set(df_new["date"]))], df_new], ignore_index=True)
    else:
        out = df_new
    out = out.sort_values(["date", "asset_class", "ticker"]).reset_index(drop=True)
    out.to_csv(HIST_CSV, index=False, encoding="utf-8-sig")
    return len(out)


def _trading_dates(start: pd.Timestamp, end: pd.Timestamp | None) -> list[pd.Timestamp]:
    taiex = db.get_prices("^TWII", start=start, end=end)
    if taiex.empty:
        return []
    return sorted(pd.to_datetime(taiex["date"]).tolist())


def _prefetch_cache(tickers: list[str], start: pd.Timestamp, end: pd.Timestamp):
    """Load each ticker's full series once and serve in-memory slices, so a long
    backfill doesn't re-query SQLite for every (ticker, as-of) pair. Returns
    (cached_get_prices, real_get_prices) — caller restores the real one when done.
    """
    real = db.get_prices
    store = {t: real(t, start=start, end=end) for t in tickers}

    def cached(ticker, start=None, end=None, mtime=None):
        df = store.get(ticker)
        if df is None or df.empty:
            return pd.DataFrame()
        out = df
        if start is not None:
            out = out[out["date"] >= pd.Timestamp(start)]
        if end is not None:
            out = out[out["date"] <= pd.Timestamp(end)]
        return out.reset_index(drop=True)

    return cached, real


def run(backfill: bool, start: str | None, end: str | None, years: int) -> int:
    if not db.DB_PATH.exists():
        print(f"DB not found at {db.DB_PATH}. Build/backfill it first.")
        return 1

    universe = db.get_universe()
    eligible = _eligible(universe)
    if eligible.empty:
        print("No eligible ETFs with prices.")
        return 1

    latest = pd.Timestamp(end) if end else pd.Timestamp(db.db_summary()["date_max"])

    if not backfill:
        df_new = scores_as_of(universe, eligible, latest)
        total = _upsert(df_new)
        print(f"[step7] recorded {len(df_new)} ETFs for {latest.date()}; "
              f"store now has {total} rows → {HIST_CSV}")
        return 0

    start_ts = pd.Timestamp(start) if start else (latest - pd.DateOffset(years=years))
    dates = _trading_dates(start_ts, latest)
    if not dates:
        print("No trading dates in range.")
        return 1

    # Pre-fetch every series once (eligible ETFs + benchmark indices), then slice.
    tickers = list(dict.fromkeys(eligible["ticker"].tolist() + list(REF_INDICES)))
    cached, real = _prefetch_cache(tickers, dates[0] - LOOKBACK, dates[-1])
    db.get_prices = cached
    try:
        frames = []
        for i, d in enumerate(dates, 1):
            frames.append(scores_as_of(universe, eligible, d))
            if i % 20 == 0 or i == len(dates):
                print(f"  [{i}/{len(dates)}] {d.date()}", flush=True)
    finally:
        db.get_prices = real

    df_new = pd.concat(frames, ignore_index=True)
    total = _upsert(df_new)
    print(f"[step7] backfilled {df_new['date'].nunique()} dates ({len(df_new)} rows) "
          f"from {start_ts.date()}; store now has {total} rows → {HIST_CSV}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true",
                    help="recompute history (default: append today only)")
    ap.add_argument("--years", type=int, default=DEFAULT_YEARS,
                    help=f"backfill this many years of history (default {DEFAULT_YEARS})")
    ap.add_argument("--start", default=None, help="explicit backfill start date (overrides --years)")
    ap.add_argument("--end", default=None, help="as-of / backfill end date (default: latest in DB)")
    args = ap.parse_args()
    return run(backfill=args.backfill, start=args.start, end=args.end, years=args.years)


if __name__ == "__main__":
    raise SystemExit(main())
