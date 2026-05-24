"""Step 6 — tag TAIEX market-regime periods (bull / correction / mini_bear / bear).

Uses a rolling drawdown-from-peak approach on ^TWII (加權指數).
Each trading day is assigned a regime label; consecutive same-label days are
grouped into period rows written to the `regimes` table.

Thresholds (drawdown from expanding ATH since window start):
    bull        :    0% to   -5%   (market is healthy)
    correction  :   -5% to  -10%   (normal pullback)
    mini_bear   :  -10% to  -20%   (significant decline)
    bear        : < -20%           (bear market)

A minimum run of MIN_TRADING_DAYS is enforced before committing a regime
change, preventing single-session noise from fragmenting the timeline.
Short periods that survive the initial pass are absorbed by their neighbours
in an iterative merge step.

Idempotent — clears all previous auto_drawdown rows for the reference ticker
before reinserting, so it is safe to re-run daily.

Run:
    python -m scripts.etf_benchmark.step6_regimes
    python -m scripts.etf_benchmark.step6_regimes --reference ^TWII
    python -m scripts.etf_benchmark.step6_regimes --thresholds 5,10,20
    python -m scripts.etf_benchmark.step6_regimes --min-days 3
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

DB_PATH = ROOT_DIR / "data" / "etf_bench" / "etf_bench.sqlite"

# ── defaults ────────────────────────────────────────────────────────────────
DEFAULT_REFERENCE   = "^TWII"
# thresholds: drawdown from ATH beyond which the next regime begins (all positive)
DEFAULT_THRESHOLDS  = (5.0, 10.0, 20.0)   # correction / mini_bear / bear
MIN_TRADING_DAYS    = 5                    # short periods shorter than this get merged


# ── regime assignment ────────────────────────────────────────────────────────
def _assign_label(dd_pct: float, thresholds: tuple[float, float, float]) -> str:
    t1, t2, t3 = thresholds
    if dd_pct >= -t1:
        return "bull"
    if dd_pct >= -t2:
        return "correction"
    if dd_pct >= -t3:
        return "mini_bear"
    return "bear"


# ── period grouping + short-period merge ─────────────────────────────────────
def _group_periods(df: pd.DataFrame) -> list[dict]:
    """Convert a date-indexed regime series into a list of period dicts."""
    periods: list[dict] = []
    rows = df.itertuples(index=False)
    cur = next(rows)
    start = cur.date
    label = cur.regime
    dd    = cur.dd_pct

    for row in rows:
        if row.regime == label:
            dd = min(dd, row.dd_pct)
        else:
            periods.append({
                "start_date": start,
                "end_date":   cur.date,
                "regime":     label,
                "severity":   round(dd, 2),
                "n_days":     0,           # filled below
            })
            start = row.date
            label = row.regime
            dd    = row.dd_pct
        cur = row

    periods.append({
        "start_date": start,
        "end_date":   cur.date,
        "regime":     label,
        "severity":   round(dd, 2),
        "n_days":     0,
    })
    return periods


def _fill_ndays(periods: list[dict], date_set: set) -> None:
    """Count trading days (from the prices calendar) inside each period."""
    sorted_dates = sorted(date_set)
    date_pos = {d: i for i, d in enumerate(sorted_dates)}
    for p in periods:
        s = date_pos.get(p["start_date"], 0)
        e = date_pos.get(p["end_date"],   0)
        p["n_days"] = e - s + 1


def _merge_short_periods(periods: list[dict], min_days: int) -> list[dict]:
    """Iteratively absorb any period shorter than min_days into an adjacent one.

    Preference: merge into a neighbour with the same regime; otherwise into
    the longer neighbour.  The last/first period is always merged backward/forward.
    Runs until no short periods remain.
    """
    changed = True
    while changed and len(periods) > 1:
        changed = False
        new: list[dict] = []
        i = 0
        while i < len(periods):
            p = periods[i]
            if p["n_days"] >= min_days:
                new.append(p)
                i += 1
                continue

            changed = True
            is_first = (i == 0)
            is_last  = (i == len(periods) - 1)

            if is_first:
                # Prepend into next
                nxt = periods[i + 1]
                periods[i + 1] = {
                    **nxt,
                    "start_date": p["start_date"],
                    "n_days":     p["n_days"] + nxt["n_days"],
                    "severity":   min(p["severity"], nxt["severity"]),
                }
            elif is_last:
                # Append into previous (already in new)
                prev = new[-1]
                new[-1] = {
                    **prev,
                    "end_date": p["end_date"],
                    "n_days":   prev["n_days"] + p["n_days"],
                    "severity": min(p["severity"], prev["severity"]),
                }
            else:
                prev = new[-1]
                nxt  = periods[i + 1]
                prefer_prev = (
                    prev["regime"] == p["regime"]
                    or (nxt["regime"] != p["regime"] and prev["n_days"] >= nxt["n_days"])
                )
                if prefer_prev:
                    new[-1] = {
                        **prev,
                        "end_date": p["end_date"],
                        "n_days":   prev["n_days"] + p["n_days"],
                        "severity": min(p["severity"], prev["severity"]),
                    }
                else:
                    periods[i + 1] = {
                        **nxt,
                        "start_date": p["start_date"],
                        "n_days":     p["n_days"] + nxt["n_days"],
                        "severity":   min(p["severity"], nxt["severity"]),
                    }
            i += 1
        periods = new
    return periods


# ── main ─────────────────────────────────────────────────────────────────────
def run_regimes(
    reference:  str   = DEFAULT_REFERENCE,
    thresholds: tuple = DEFAULT_THRESHOLDS,
    min_days:   int   = MIN_TRADING_DAYS,
) -> int:
    if not DB_PATH.exists():
        print(f"[step6] DB not found at {DB_PATH}. Run step2 + step3 first.")
        return 1

    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query(
            "SELECT date, close FROM prices WHERE ticker = ? ORDER BY date",
            conn, params=[reference],
        )

    if df.empty:
        print(f"[step6] No prices found for {reference}. Run step3 first.")
        return 1

    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.dropna(subset=["close"]).copy()
    df["ath"]    = df["close"].cummax()
    df["dd_pct"] = (df["close"] - df["ath"]) / df["ath"] * 100.0
    df["regime"] = df["dd_pct"].apply(_assign_label, args=(thresholds,))

    periods = _group_periods(df)
    _fill_ndays(periods, set(df["date"].tolist()))
    periods = _merge_short_periods(periods, min_days)

    # Write to DB — idempotent: clear auto_drawdown rows for this reference first
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "DELETE FROM regimes WHERE reference_index = ? AND source = 'auto_drawdown'",
            (reference,),
        )
        for p in periods:
            conn.execute(
                "INSERT INTO regimes "
                "(start_date, end_date, regime, severity, reference_index, notes, source) "
                "VALUES (?, ?, ?, ?, ?, ?, 'auto_drawdown')",
                (
                    p["start_date"].isoformat(),
                    p["end_date"].isoformat(),
                    p["regime"],
                    p["severity"],
                    reference,
                    f"{p['n_days']} trading days",
                ),
            )
        conn.commit()

    print(f"[step6] {reference}: {len(df)} trading days → {len(periods)} regime periods")
    print()
    t_map = {"bull": "多頭", "correction": "修正", "mini_bear": "小熊", "bear": "熊市"}
    for p in periods:
        label = t_map.get(p["regime"], p["regime"])
        print(
            f"  {p['start_date']} → {p['end_date']}  "
            f"{p['regime']:12s}({label})  "
            f"worst DD {p['severity']:+.1f}%  "
            f"({p['n_days']} trading days)"
        )
    print()

    by_regime: dict[str, int] = {}
    for p in periods:
        by_regime[p["regime"]] = by_regime.get(p["regime"], 0) + 1
    print("  Summary by regime:")
    for regime, count in sorted(by_regime.items()):
        print(f"    {regime:12s}: {count} period(s)")
    print()
    print("[step6] written to regimes table")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reference",  default=DEFAULT_REFERENCE,
                    help=f"Reference ticker in prices table (default: {DEFAULT_REFERENCE})")
    ap.add_argument("--thresholds", default=",".join(str(t) for t in DEFAULT_THRESHOLDS),
                    help="Comma-separated drawdown thresholds in %% for correction,mini_bear,bear "
                         f"(default: {','.join(str(t) for t in DEFAULT_THRESHOLDS)})")
    ap.add_argument("--min-days",   type=int, default=MIN_TRADING_DAYS,
                    help=f"Minimum trading days before a regime change is committed (default: {MIN_TRADING_DAYS})")
    args = ap.parse_args()

    try:
        thresholds = tuple(float(t) for t in args.thresholds.split(","))
        if len(thresholds) != 3:
            raise ValueError
    except (ValueError, AttributeError):
        print("--thresholds must be three comma-separated numbers, e.g. 5,10,20")
        return 1

    return run_regimes(
        reference=args.reference,
        thresholds=thresholds,
        min_days=args.min_days,
    )


if __name__ == "__main__":
    sys.exit(main())
