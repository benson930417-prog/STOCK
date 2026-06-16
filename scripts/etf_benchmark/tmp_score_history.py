"""TEMP / local-only — backfill the 綜合評分 history for the pocket list and plot it.

This is a throwaway bootstrap so we can see the score curve from 2026-02-23 → now
before wiring anything into the LINE bot or the daily job. It is NOT imported by any
service. Once the graph looks right, this logic folds into `step7_score.py`.

It replays the *exact* live scorer (`_build_score_table`, with `as_of=`) once per
trading day, anchored at START (expanding window). A fund only appears once its window
has ≥ MIN_DAYS trading days, so the early, statistically-thin period is hidden.

Needs the SQLite DB to exist:  data/etf_bench/etf_bench.sqlite
    • run on the server where the daily job built it, OR
    • build locally first:  python -m scripts.etf_benchmark.step1_universe
                            python -m scripts.etf_benchmark.step2_schema --reset
                            python -m scripts.etf_benchmark.step3_backfill

Run:
    python -m scripts.etf_benchmark.tmp_score_history
Outputs (under data/):
    score_history.csv   — date × ticker composite scores
    score_history.png   — line chart
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Quieten the "missing ScriptRunContext" noise from importing the Streamlit-cached db.
logging.getLogger("streamlit").setLevel(logging.ERROR)

from scripts.etf_benchmark import db                       # noqa: E402
from src.ui.etf_compare_tab import _build_score_table      # noqa: E402

# ── knobs ────────────────────────────────────────────────────────────────────
# 00997A excluded — only listed from April, too short to compare on the 2/23 window.
POCKET = ["0050", "00981A", "00988A", "00990A", "00991A", "00992A"]
START    = pd.Timestamp("2026-02-23")   # pocket inception anchor
MIN_DAYS = 30                           # don't plot a score until ≥ this many trading days
WEIGHTS  = {"efficiency": 1.0, "asymmetry": 1.0, "consistency": 1.0}  # equal = fair

OUT_CSV = ROOT_DIR / "data" / "score_history.csv"
OUT_PNG = ROOT_DIR / "data" / "score_history.png"


def _trading_dates() -> list[pd.Timestamp]:
    taiex = db.get_prices("^TWII", start=START)
    if taiex.empty:
        return []
    return sorted(pd.to_datetime(taiex["date"]).tolist())


def build_history() -> pd.DataFrame:
    universe = db.get_universe()
    dates = _trading_dates()
    if not dates:
        raise SystemExit("No ^TWII prices since START — is the DB built and backfilled?")

    per_date: dict[pd.Timestamp, pd.Series] = {}
    for i, d in enumerate(dates):
        if (i + 1) < MIN_DAYS:                 # need MIN_DAYS observations in the window
            continue
        sdf = _build_score_table(POCKET, universe, START, WEIGHTS, as_of=d)
        per_date[d] = sdf["綜合評分"]           # Series indexed by ticker

    if not per_date:
        raise SystemExit(f"Not enough history yet (need ≥ {MIN_DAYS} trading days from {START.date()}).")

    hist = pd.DataFrame(per_date).T            # index = date, columns = ticker
    hist = hist.reindex(columns=POCKET)        # stable column order
    hist.index.name = "date"
    return hist


def plot_history(hist: pd.DataFrame, universe: pd.DataFrame) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    # Best-effort CJK font (same proven search list as scripts/generate_quote_card.py).
    for fp in (
        str(ROOT_DIR / "data" / "fonts" / "NotoSansCJK-Regular.ttc"),
        str(ROOT_DIR / "data" / "fonts" / "NotoSansTC-Regular.otf"),
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKtc-Regular.otf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/arphic/uming.ttc",
        "/usr/share/fonts/truetype/arphic/ukai.ttc",
        r"C:\Windows\Fonts\msjh.ttc",
    ):
        if Path(fp).exists():
            font_manager.fontManager.addfont(fp)
            matplotlib.rcParams["font.family"] = font_manager.FontProperties(fname=fp).get_name()
            break
    matplotlib.rcParams["axes.unicode_minus"] = False

    name_map = dict(zip(universe["ticker"], universe["name"]))
    fig, ax = plt.subplots(figsize=(12, 6))
    for t in hist.columns:
        s = hist[t].dropna()
        if s.empty:
            continue
        ax.plot(s.index, s.values, marker="o", markersize=2, linewidth=1.6,
                label=f"{t} {name_map.get(t, '')}")
    ax.axhline(50, color="#888", linestyle="--", linewidth=1)
    ax.set_title(f"口袋 ETF 綜合評分走勢（自 {START.date()}，等權）")
    ax.set_ylabel("綜合評分 (0–100)")
    ax.set_ylim(0, 100)
    ax.grid(True, alpha=0.25)
    ax.legend(loc="upper left", fontsize=8, ncol=2)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(OUT_PNG, dpi=140)
    print(f"[tmp] wrote {OUT_PNG}")


def main() -> int:
    if not db.DB_PATH.exists():
        print(f"DB not found at {db.DB_PATH}. Build/backfill it first (see module docstring).")
        return 1
    hist = build_history()
    hist.round(1).to_csv(OUT_CSV, encoding="utf-8-sig")
    print(f"[tmp] wrote {OUT_CSV}  ({hist.shape[0]} dates × {hist.shape[1]} tickers)")
    print(hist.round(1).tail(10).to_string())
    plot_history(hist, db.get_universe())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
