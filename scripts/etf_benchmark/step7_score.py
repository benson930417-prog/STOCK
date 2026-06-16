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
    python -m scripts.etf_benchmark.step7_score                      # append today
    python -m scripts.etf_benchmark.step7_score --backfill           # history from START
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
HIST_CSV     = ROOT_DIR / "data" / "etf_bench" / "score_history.csv"
DEFAULT_START = "2026-02-23"                 # earliest date to backfill the history
LOOKBACK      = pd.DateOffset(years=1)       # trailing window for the metrics
MIN_DAYS      = 30                           # ≥ this many of the fund's OWN trading days
WEIGHTS       = {"efficiency": 1.0, "asymmetry": 1.0, "consistency": 1.0}  # only pillars are stored

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


def run(backfill: bool, start: str, end: str | None) -> int:
    if not db.DB_PATH.exists():
        print(f"DB not found at {db.DB_PATH}. Build/backfill it first.")
        return 1

    universe = db.get_universe()
    eligible = _eligible(universe)
    if eligible.empty:
        print("No eligible ETFs with prices.")
        return 1

    if backfill:
        dates = _trading_dates(pd.Timestamp(start), pd.Timestamp(end) if end else None)
        if not dates:
            print("No trading dates in range.")
            return 1
        frames = []
        for i, d in enumerate(dates, 1):
            frames.append(scores_as_of(universe, eligible, d))
            if i % 10 == 0 or i == len(dates):
                print(f"  [{i}/{len(dates)}] {d.date()}", flush=True)
        df_new = pd.concat(frames, ignore_index=True)
        total = _upsert(df_new)
        print(f"[step7] backfilled {df_new['date'].nunique()} dates "
              f"({len(df_new)} rows); store now has {total} rows → {HIST_CSV}")
    else:
        as_of = pd.Timestamp(end) if end else pd.Timestamp(db.db_summary()["date_max"])
        df_new = scores_as_of(universe, eligible, as_of)
        total = _upsert(df_new)
        print(f"[step7] recorded {len(df_new)} ETFs for {as_of.date()}; "
              f"store now has {total} rows → {HIST_CSV}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true",
                    help="recompute the whole history from --start (default: just today)")
    ap.add_argument("--start", default=DEFAULT_START, help=f"backfill start (default {DEFAULT_START})")
    ap.add_argument("--end", default=None, help="as-of / backfill end date (default: latest in DB)")
    args = ap.parse_args()
    return run(backfill=args.backfill, start=args.start, end=args.end)


if __name__ == "__main__":
    raise SystemExit(main())
