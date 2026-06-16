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

# Quieten the "No runtime found" / "missing ScriptRunContext" noise from the
# Streamlit-cached db helpers when run as a plain CLI.
for _n in ("streamlit", "streamlit.runtime.caching.cache_data_api",
           "streamlit.runtime.caching"):
    logging.getLogger(_n).setLevel(logging.ERROR)

from scripts.etf_benchmark import db                       # noqa: E402
from src.ui.etf_compare_tab import _build_score_table      # noqa: E402

# ── knobs ────────────────────────────────────────────────────────────────────
# 00997A excluded — only listed from April, too short to compare on the 2/23 window.
POCKET = ["0050", "00981A", "00988A", "00990A", "00991A", "00992A"]
START    = pd.Timestamp("2026-02-23")   # pocket inception anchor
MIN_DAYS = 30                           # each ETF is scored only after it has ≥ this many
                                        # trading days of its OWN data (so a fund listed later
                                        # starts 30 trading days after its own listing)
WEIGHTS  = {"efficiency": 1.0, "asymmetry": 1.0, "consistency": 1.0}  # equal = fair

OUT_CSV = ROOT_DIR / "data" / "score_history.csv"
OUT_PNG = ROOT_DIR / "data" / "score_history.png"

# ── chart style (edit freely, then re-run) ───────────────────────────────────
STYLE = {
    "bg":         "#0e1117",   # figure / axes background (matches dashboard dark)
    "text":       "#e6e6e6",   # title / axis text
    "muted":      "#8b95a5",   # subtitle / ticks
    "grid":       "#222a35",   # gridlines
    "ref_line":   "#5b6675",   # the 50 reference line
    "line_width": 2.4,
    "figsize":    (13, 7),
    "dpi":        160,
    "title":      "口袋 ETF 綜合評分走勢",
    # one colour per fund, in POCKET order — tuned for a dark background
    "palette": ["#5aa9ff", "#ff9f43", "#4ade80", "#c084fc", "#f7d154", "#fb7185"],
    "label_gap":  2.6,         # min vertical gap (score units) between end labels
    "y_zoom":     True,        # auto-zoom y to the data (vs fixed 0-100)
    "y_pad":      6.0,         # padding above/below the data when zoomed
    "min_alpha":  0.45,        # line opacity at lowest confidence (fades in as sample grows)
    "compress":   True,        # compress raw standing toward 50 by *current* confidence
                               #   (calm band, matches the live table; no fan-out artefact)
}


def _trading_dates() -> list[pd.Timestamp]:
    taiex = db.get_prices("^TWII", start=START)
    if taiex.empty:
        return []
    return sorted(pd.to_datetime(taiex["date"]).tolist())


def build_history() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (raw_scores, confidence) — both date × ticker.

    Scores are the *raw* standing (shrink=False) so the trend reflects real ranking
    change; confidence (0–1) is returned separately and drives the line fade.
    """
    universe = db.get_universe()
    dates = _trading_dates()
    if not dates:
        raise SystemExit("No ^TWII prices since START — is the DB built and backfilled?")

    score_by_date: dict[pd.Timestamp, pd.Series] = {}
    conf_by_date: dict[pd.Timestamp, pd.Series] = {}
    for i, d in enumerate(dates):
        if (i + 1) < MIN_DAYS:                 # global fast-skip: no fund can have MIN_DAYS yet
            continue
        sdf = _build_score_table(POCKET, universe, START, WEIGHTS, as_of=d, shrink=False)
        # Per-fund gate: only keep a fund's score once IT has ≥ MIN_DAYS of its own data,
        # so a later-listed ETF's line begins 30 trading days after its own listing.
        score_by_date[d] = sdf["綜合評分"].where(sdf["n_days"] >= MIN_DAYS)
        conf_by_date[d] = sdf["_conf"]

    if not score_by_date:
        raise SystemExit(f"Not enough history yet (need ≥ {MIN_DAYS} trading days from {START.date()}).")

    hist = pd.DataFrame(score_by_date).T.reindex(columns=POCKET)
    conf = pd.DataFrame(conf_by_date).T.reindex(columns=POCKET)
    hist.index.name = conf.index.name = "date"
    return hist, conf


def _load_cjk_font():
    from matplotlib import font_manager
    # Same proven search list as scripts/generate_quote_card.py.
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
            return font_manager.FontProperties(fname=fp).get_name()
    return None


def _spread(values: list[float], min_gap: float, lo: float, hi: float) -> list[float]:
    """Nudge label y-positions apart so end labels don't overlap (keeps order)."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    adj = list(values)
    for k in range(1, len(order)):
        prev, cur = order[k - 1], order[k]
        if adj[cur] - adj[prev] < min_gap:
            adj[cur] = adj[prev] + min_gap
    top = adj[order[-1]]
    if top > hi:                                  # overflowed top → shift the stack down
        shift = top - hi
        for i in order:
            adj[i] = max(lo, adj[i] - shift)
    return adj


def plot_history(hist: pd.DataFrame, conf: pd.DataFrame, universe: pd.DataFrame) -> None:
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from matplotlib.collections import LineCollection
    from matplotlib.colors import to_rgb

    fam = _load_cjk_font()
    if fam:
        matplotlib.rcParams["font.family"] = fam
    matplotlib.rcParams["axes.unicode_minus"] = False

    name_map = dict(zip(universe["ticker"], universe["name"]))
    colors = {t: STYLE["palette"][i % len(STYLE["palette"])]
              for i, t in enumerate(hist.columns)}

    fig, ax = plt.subplots(figsize=STYLE["figsize"])
    fig.patch.set_facecolor(STYLE["bg"])
    ax.set_facecolor(STYLE["bg"])
    fig.subplots_adjust(left=0.06, right=0.78, top=0.86, bottom=0.10)

    all_y: list[float] = []
    end_pts: list[tuple] = []                     # (y, ticker)
    min_a = STYLE["min_alpha"]
    for t in hist.columns:
        s = hist[t].dropna()
        if s.empty:
            continue
        c = conf[t].reindex(s.index).fillna(0.0).to_numpy()
        x = mdates.date2num(s.index.to_pydatetime())
        y = s.to_numpy(dtype=float)
        all_y.extend(y.tolist())

        # Per-segment fade: opacity grows with that day's confidence.
        pts = np.column_stack([x, y]).reshape(-1, 1, 2)
        segs = np.concatenate([pts[:-1], pts[1:]], axis=1)
        seg_conf = np.minimum(c[:-1], c[1:])
        rgb = to_rgb(colors[t])
        rgba = [(rgb[0], rgb[1], rgb[2], min_a + (1.0 - min_a) * float(cc)) for cc in seg_conf]
        ax.add_collection(LineCollection(segs, colors=rgba, linewidths=STYLE["line_width"],
                                         capstyle="round", zorder=3))
        end_pts.append((float(y[-1]), t))

    # Auto-zoom (or fixed 0-100)
    if STYLE["y_zoom"] and all_y:
        lo = max(0.0, min(all_y) - STYLE["y_pad"])
        hi = min(100.0, max(all_y) + STYLE["y_pad"])
    else:
        lo, hi = 0.0, 100.0
    ax.set_ylim(lo, hi)
    ax.set_xlim(hist.index.min(), hist.index.max())

    # 50 = "selection median" reference (only if in view)
    if lo <= 50 <= hi:
        ax.axhline(50, color=STYLE["ref_line"], linestyle=(0, (5, 4)), linewidth=1.1, zorder=1)
        ax.text(0.004, 50, " 50 中位", transform=ax.get_yaxis_transform(),
                color=STYLE["ref_line"], fontsize=9, va="bottom", ha="left")

    # End-of-line labels: "name  score", de-overlapped within the visible range
    end_pts.sort()
    ys = _spread([y for y, _ in end_pts], STYLE["label_gap"], lo + 1, hi - 1)
    for (orig_y, t), y in zip(end_pts, ys):
        ax.text(1.012, y, f"{name_map.get(t, t)}  {orig_y:.0f}",
                transform=ax.get_yaxis_transform(), color=colors[t],
                fontsize=11, fontweight="bold", va="center", ha="left")

    ax.set_title(STYLE["title"], color=STYLE["text"], fontsize=20,
                 fontweight="bold", loc="left", pad=26)
    ax.text(0, 1.03, f"自 {START.date()} ｜ 等權 ｜ 線條由淡轉濃＝樣本越長越可信 ｜ 數值＝口袋內相對評分（已依信賴度壓縮）",
            transform=ax.transAxes, color=STYLE["muted"], fontsize=11, va="bottom")

    ax.grid(True, axis="y", color=STYLE["grid"], linewidth=0.8, zorder=0)
    ax.tick_params(colors=STYLE["muted"])
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(STYLE["grid"])
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))

    fig.savefig(OUT_PNG, dpi=STYLE["dpi"], facecolor=STYLE["bg"])
    print(f"[tmp] wrote {OUT_PNG}")


def main() -> int:
    if not db.DB_PATH.exists():
        print(f"DB not found at {db.DB_PATH}. Build/backfill it first (see module docstring).")
        return 1
    hist_raw, conf = build_history()
    if STYLE["compress"]:
        # Compress the raw standing toward 50 by each fund's *latest* confidence,
        # held constant across the time axis so a stable standing draws a flat line.
        conf_latest = conf.ffill().iloc[-1]
        hist = 50.0 + (hist_raw - 50.0).mul(conf_latest, axis=1)
    else:
        hist = hist_raw
    hist.round(1).to_csv(OUT_CSV, encoding="utf-8-sig")
    print(f"[tmp] wrote {OUT_CSV}  ({hist.shape[0]} dates × {hist.shape[1]} tickers)")
    print(hist.round(1).tail(10).to_string())
    plot_history(hist, conf, db.get_universe())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
