"""Independent verifier for the ETF 綜合評分 history.

This script DELIBERATELY shares no code with the app (no imports from
src/ or scripts/). It re-derives the three score pillars (效率/不對稱/一致性)
from raw prices using its own SQL reader and its own metric maths, then compares
the result against the app-produced data/etf_bench/score_history.csv.

If the app's pipeline is correct, the recomputed pillars match the stored ones.

Methodology mirrored (kept in sync with the app on purpose):
  • trailing 1-year window ending at the as-of date, on adj_close (fallback close)
  • daily returns clipped to ±50%
  • efficiency  = mean rank of [Sortino, Calmar]
  • asymmetry   = rank of (up_capture − down_capture), only if a benchmark exists
                  and R² ≥ 0.20
  • consistency = mean rank of [batting average, −tracking error, −volatility]
  • each metric ranked 0-100 WITHIN ITS ASSET CLASS, among funds with ≥20 days
  • only funds with ≥30 of their own trading days are reported

Run (on the server, where the sqlite lives):
    python score_verify/verify_scores.py
    python score_verify/verify_scores.py --tickers 00981A,00988A,0050 --date 2026-06-16
    python score_verify/verify_scores.py --all          # every stored fund on the date
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB  = ROOT / "data" / "etf_bench" / "etf_bench.sqlite"
DEFAULT_CSV = ROOT / "data" / "etf_bench" / "score_history.csv"

TRADING_DAYS   = 252.0
RISK_FREE      = 0.0
RET_CLIP       = 0.50
MIN_RANK_DAYS  = 20     # a fund must have this many days to enter the ranking
MIN_REPORT_DAYS = 30    # only funds with this many days are stored/compared
R2_MIN         = 0.20
LOOKBACK       = pd.DateOffset(years=1)
TOL            = 0.05   # allowed |diff| in a pillar score (CSV is stored to 2 dp)

TW_EQUITY = {"passive_equity", "active_equity", "leveraged"}
ASSET_CLASS = {
    "passive_equity": "equity", "active_equity": "equity", "leveraged": "equity",
    "bond": "bond", "commodity": "commodity", "other": "other",
}
PILLAR_MEMBERS = {
    "eff": [("sortino", True), ("calmar", True)],
    "asy": [("capture_spread", True)],
    "con": [("batting", True), ("tracking_err", False), ("ann_vol", False)],
}


# ── raw SQL data access (independent of db.py) ───────────────────────────────
def load_universe(conn: sqlite3.Connection) -> pd.DataFrame:
    etfs = pd.read_sql_query("SELECT * FROM etfs", conn)
    cnt = pd.read_sql_query("SELECT ticker, COUNT(*) n FROM prices GROUP BY ticker", conn)
    etfs = etfs.merge(cnt, on="ticker", how="left")
    etfs["has_prices"] = etfs["n"].fillna(0) > 0
    etfs["asset_class"] = etfs["fund_type"].map(ASSET_CLASS)
    return etfs


def load_prices(conn, ticker, start, end) -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT date, close, adj_close FROM prices "
        "WHERE ticker=? AND date>=? AND date<=? ORDER BY date",
        conn, params=[ticker, pd.Timestamp(start).date().isoformat(),
                      pd.Timestamp(end).date().isoformat()],
    )
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


# ── independent metric maths ─────────────────────────────────────────────────
def daily_returns(price: pd.Series) -> pd.Series:
    return price.astype(float).pct_change().dropna().clip(-RET_CLIP, RET_CLIP)


def ann_return(price: pd.Series, n: int):
    if len(price) < 2 or n <= 0:
        return None
    total = float(price.iloc[-1]) / float(price.iloc[0])
    if total <= 0:
        return None
    return total ** (TRADING_DAYS / n) - 1.0


def max_drawdown(price: pd.Series) -> float:
    return float((price / price.cummax() - 1.0).min())


def downside_dev(r: pd.Series):
    if r.empty:
        return None
    below = (r).clip(upper=0.0)
    v = float((below ** 2).mean()) ** 0.5 * (TRADING_DAYS ** 0.5)
    return v if v > 0 else None


def capture(fund_r: pd.Series, bench_r: pd.Series):
    j = pd.concat([fund_r.rename("f"), bench_r.rename("b")], axis=1, join="inner").dropna()
    if len(j) < 10:
        return None
    f, b = j["f"], j["b"]
    up, dn = b > 0, b < 0
    cum = lambda x: float((1.0 + x).prod() - 1.0)
    ub, db = cum(b[up]), cum(b[dn])
    up_cap = cum(f[up]) / ub if abs(ub) > 1e-6 else None
    dn_cap = cum(f[dn]) / db if abs(db) > 1e-6 else None
    r2 = float(f.corr(b) ** 2) if (f.std(ddof=1) > 0 and b.std(ddof=1) > 0) else None
    return {"up": up_cap, "dn": dn_cap, "r2": r2,
            "batting": float((f > b).mean()),
            "tracking_err": float((f - b).std(ddof=1) * (TRADING_DAYS ** 0.5))}


def benchmark_for(row) -> str | None:
    blob = " ".join(str(row.get(c, "") or "") for c in
                    ("name", "full_name", "en_name", "tracked_index")).upper()
    if "那斯達克" in blob or "NASDAQ" in blob:
        return "^IXIC"
    if "標普" in blob or "S&P" in blob or "SP500" in blob:
        return "^GSPC"
    if "道瓊" in blob or "DOW JONES" in blob:
        return "^DJI"
    if row.get("fund_type") in TW_EQUITY:
        return "^TWII"
    return None


def rank01(series: pd.Series, higher_better: bool) -> pd.Series:
    out = pd.Series(index=series.index, dtype=float)
    vals = series.dropna()
    if vals.empty:
        return out
    if len(vals) == 1:
        out.loc[vals.index[0]] = 50.0
        return out
    rr = vals.rank(method="average", ascending=higher_better)
    out.loc[vals.index] = (rr - 1.0) / (len(vals) - 1.0) * 100.0
    return out


# ── per-asset-class pillar computation ───────────────────────────────────────
def class_pillars(conn, members: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    baseline = pd.Timestamp(as_of) - LOOKBACK
    bench_cache: dict[str, pd.Series | None] = {}

    def bench_ret(idx):
        if idx is None:
            return None
        if idx not in bench_cache:
            bdf = load_prices(conn, idx, baseline, as_of)
            if bdf.empty:
                bench_cache[idx] = None
            else:
                s = bdf["close"].astype(float)
                s.index = bdf["date"]
                bench_cache[idx] = daily_returns(s)
        return bench_cache[idx]

    recs = []
    for _, row in members.iterrows():
        df = load_prices(conn, row["ticker"], baseline, as_of)
        if df.empty:
            continue
        price = df["adj_close"].fillna(df["close"]).astype(float)
        price.index = df["date"]
        r = daily_returns(price)
        n = len(r)
        if n < MIN_RANK_DAYS:
            continue
        rec = {"ticker": row["ticker"], "n_days": n}
        a = ann_return(price, n)
        mdd = max_drawdown(price)
        dd = downside_dev(r)
        if a is not None and dd:
            rec["sortino"] = (a - RISK_FREE) / dd
        if a is not None and mdd < 0:
            rec["calmar"] = a / abs(mdd)
        if n > 2:
            rec["ann_vol"] = float(r.std(ddof=1) * (TRADING_DAYS ** 0.5))
        br = bench_ret(benchmark_for(row))
        if br is not None:
            cap = capture(r, br)
            if cap and cap["r2"] is not None and cap["r2"] >= R2_MIN:
                rec["batting"] = cap["batting"]
                rec["tracking_err"] = cap["tracking_err"]
                if cap["up"] is not None and cap["dn"] is not None:
                    rec["capture_spread"] = cap["up"] - cap["dn"]
        recs.append(rec)

    sdf = pd.DataFrame(recs).set_index("ticker")
    metric_scores = {}
    for _, members_list in PILLAR_MEMBERS.items():
        for m, hb in members_list:
            if m in sdf.columns and m not in metric_scores:
                metric_scores[m] = rank01(sdf[m], hb)
    for p, members_list in PILLAR_MEMBERS.items():
        cols = [metric_scores[m] for m, _ in members_list if m in metric_scores]
        sdf[p] = pd.concat(cols, axis=1).mean(axis=1, skipna=True) if cols else np.nan
    return sdf


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--csv", default=str(DEFAULT_CSV))
    ap.add_argument("--date", default=None, help="as-of date (default: latest in the csv)")
    ap.add_argument("--tickers", default="0050,00981A,00988A,00990A,00991A,00992A")
    ap.add_argument("--all", action="store_true", help="verify every fund stored on the date")
    args = ap.parse_args()

    db_path, csv_path = Path(args.db), Path(args.csv)
    if not db_path.exists():
        print(f"DB not found: {db_path}"); return 1
    if not csv_path.exists():
        print(f"score_history.csv not found: {csv_path}"); return 1

    app = pd.read_csv(csv_path)
    app["date"] = app["date"].astype(str)
    as_of = pd.Timestamp(args.date) if args.date else pd.Timestamp(sorted(app["date"].unique())[-1])
    day = as_of.date().isoformat()
    app_day = app[app["date"] == day].set_index("ticker")
    if app_day.empty:
        print(f"No app rows for {day}. Available last: {sorted(app['date'].unique())[-3:]}"); return 1

    want = sorted(app_day.index) if args.all else [t.strip() for t in args.tickers.split(",") if t.strip()]

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    uni = load_universe(conn)
    eligible = uni[uni["has_prices"] & uni["market"].isin(["TWSE", "TPEx"]) & uni["asset_class"].notna()]

    # compute each needed asset class once
    classes = sorted(set(eligible[eligible["ticker"].isin(want)]["asset_class"]))
    recomputed = {}
    for ac in classes:
        members = eligible[eligible["asset_class"] == ac]
        recomputed[ac] = class_pillars(conn, members, as_of)
    conn.close()

    print(f"\n  Verifying {day}  ({len(want)} funds)   tol=±{TOL}\n")
    header = f"  {'ticker':8s} {'pillar':4s} {'app':>7s} {'verify':>7s} {'diff':>7s}  status"
    print(header); print("  " + "-" * (len(header) - 2))
    n_ok = n_bad = n_skip = 0
    for t in want:
        ac = eligible.loc[eligible["ticker"] == t, "asset_class"]
        if ac.empty or t not in recomputed.get(ac.iloc[0], pd.DataFrame()).index or t not in app_day.index:
            print(f"  {t:8s} —     (not comparable / below 30-day gate)"); n_skip += 1; continue
        rc = recomputed[ac.iloc[0]].loc[t]
        if int(rc["n_days"]) < MIN_REPORT_DAYS:
            print(f"  {t:8s} —     (below 30-day gate)"); n_skip += 1; continue
        for col, en in (("eff", "eff"), ("asy", "asy"), ("con", "con")):
            av = app_day.loc[t, en]
            vv = rc[col]
            a_na, v_na = pd.isna(av), pd.isna(vv)
            if a_na and v_na:
                print(f"  {t:8s} {col:4s} {'—':>7s} {'—':>7s} {'—':>7s}  OK (both NaN)"); n_ok += 1; continue
            if a_na != v_na:
                print(f"  {t:8s} {col:4s} {('—' if a_na else f'{av:.2f}'):>7s} "
                      f"{('—' if v_na else f'{vv:.2f}'):>7s} {'NaN?':>7s}  MISMATCH"); n_bad += 1; continue
            diff = float(vv) - float(av)
            ok = abs(diff) <= TOL
            print(f"  {t:8s} {col:4s} {av:7.2f} {vv:7.2f} {diff:+7.2f}  {'OK' if ok else 'MISMATCH'}")
            n_ok += ok; n_bad += (not ok)

    print("\n  " + "-" * (len(header) - 2))
    verdict = "ALIGNED" if n_bad == 0 else f"{n_bad} MISMATCHES"
    print(f"  {verdict}   ({n_ok} ok, {n_bad} bad, {n_skip} skipped)\n")
    return 0 if n_bad == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
