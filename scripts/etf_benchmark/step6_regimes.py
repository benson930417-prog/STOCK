"""Step 6 — tag market regimes via ZigZag swing detection on a reference index.

Why ZigZag (vs rolling drawdown):
    Drawdown-from-cummax over a 2-year window anchors to a stale absolute
    peak, so multi-percent intra-trend swings get classified as "bull" even
    when the market actually corrected 7-10% recently.  ZigZag identifies
    LOCAL peaks and troughs and classifies the leg between them, which is
    what a human reads off a chart.

Algorithm (single-pass, confirmation-based):
    1. Walk forward through closes
    2. Track running extreme since last confirmed pivot
    3. The moment price reverses ≥ SWING_THRESHOLD_PCT from running extreme,
       confirm the prior extreme as a pivot and flip direction
    4. Each segment between adjacent pivots is one "leg"

Classification of each leg:
    bull        : up-leg of any magnitude
    correction  : down-leg  5%  ≤ |mag| < 10%
    mini_bear   : down-leg 10%  ≤ |mag| < 20%
    bear        : down-leg       |mag| ≥ 20%

Trading days are taken straight from the prices table (n = number of
observations in the leg) — these are the weights the compare tab uses.

Idempotent — clears all auto_zigzag rows for the reference ticker before
reinserting, safe to re-run daily.

Run:
    python -m scripts.etf_benchmark.step6_regimes
    python -m scripts.etf_benchmark.step6_regimes --reference ^TWII
    python -m scripts.etf_benchmark.step6_regimes --threshold 5.0
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

DB_PATH = ROOT_DIR / "data" / "etf_bench" / "etf_bench.sqlite"

DEFAULT_REFERENCE      = "^TWII"
DEFAULT_THRESHOLD_PCT  = 5.0        # ≥ this % reversal confirms a pivot
SOURCE_TAG             = "auto_zigzag"


# ── ZigZag pivot detection ──────────────────────────────────────────────────
def zigzag_pivots(prices: np.ndarray, threshold_pct: float) -> list[int]:
    """Return indices of confirmed swing pivots, including endpoints.

    Pivots alternate high/low. The final pivot is "tentative" (the current
    open leg's extreme), included so the most-recent partial leg is captured.
    """
    n = len(prices)
    if n < 2:
        return list(range(n))

    pivot_indices: list[int] = [0]
    last_pivot_idx   = 0
    last_pivot_price = float(prices[0])

    # Extreme since last_pivot in the current trend direction
    cur_ext_idx   = 0
    cur_ext_price = float(prices[0])
    cur_trend: str | None = None        # 'up' / 'down' / None

    for i in range(1, n):
        p = float(prices[i])

        if cur_trend is None:
            # Trend not yet established. Track most extreme deviation from start.
            move_pct = (p - last_pivot_price) / last_pivot_price * 100.0
            if abs(move_pct) >= threshold_pct:
                cur_trend     = "up" if move_pct > 0 else "down"
                cur_ext_idx   = i
                cur_ext_price = p
            elif abs(p - last_pivot_price) > abs(cur_ext_price - last_pivot_price):
                cur_ext_idx   = i
                cur_ext_price = p
            continue

        if cur_trend == "up":
            if p > cur_ext_price:
                cur_ext_idx, cur_ext_price = i, p
            elif (cur_ext_price - p) / cur_ext_price * 100.0 >= threshold_pct:
                # Confirm cur_ext as a high pivot; flip to downtrend
                pivot_indices.append(cur_ext_idx)
                last_pivot_idx, last_pivot_price = cur_ext_idx, cur_ext_price
                cur_trend = "down"
                cur_ext_idx, cur_ext_price = i, p
        else:  # 'down'
            if p < cur_ext_price:
                cur_ext_idx, cur_ext_price = i, p
            elif (p - cur_ext_price) / cur_ext_price * 100.0 >= threshold_pct:
                pivot_indices.append(cur_ext_idx)
                last_pivot_idx, last_pivot_price = cur_ext_idx, cur_ext_price
                cur_trend = "up"
                cur_ext_idx, cur_ext_price = i, p

    # Tentative final pivot at the current open leg's extreme
    if cur_trend is None:
        pivot_indices.append(n - 1)
    else:
        if cur_ext_idx > pivot_indices[-1]:
            pivot_indices.append(cur_ext_idx)
        # If the current price has moved further than cur_ext since the extreme
        # was recorded, also include the endpoint so the open leg covers to today.
        if (n - 1) > pivot_indices[-1]:
            pivot_indices.append(n - 1)

    return pivot_indices


# ── leg classification ──────────────────────────────────────────────────────
def classify_leg(magnitude_pct: float, threshold_pct: float) -> str:
    """Magnitude is signed: positive = up-leg, negative = down-leg.

    Down-leg buckets:
        threshold ≤ |mag| < 10%   → correction
        10%       ≤ |mag| < 20%   → mini_bear
        |mag|     ≥ 20%           → bear
    Sub-threshold down moves (only possible on the open last leg) → bull.
    """
    if magnitude_pct >= 0:
        return "bull"
    mag = abs(magnitude_pct)
    if mag < threshold_pct:
        return "bull"
    if mag < 10.0:
        return "correction"
    if mag < 20.0:
        return "mini_bear"
    return "bear"


# ── main ────────────────────────────────────────────────────────────────────
def run_regimes(reference: str = DEFAULT_REFERENCE,
                threshold_pct: float = DEFAULT_THRESHOLD_PCT) -> int:
    if not DB_PATH.exists():
        print(f"[step6] DB not found at {DB_PATH}. Run step2 + step3 first.")
        return 1

    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query(
            "SELECT date, close FROM prices WHERE ticker = ? AND close IS NOT NULL "
            "ORDER BY date",
            conn, params=[reference],
        )

    if df.empty or len(df) < 2:
        print(f"[step6] No prices found for {reference}. Run step3 first.")
        return 1

    df["date"]   = pd.to_datetime(df["date"]).dt.date
    prices       = df["close"].to_numpy(dtype=float)
    dates        = df["date"].tolist()

    pivot_idxs   = zigzag_pivots(prices, threshold_pct)

    # Build leg rows from consecutive pivots
    legs: list[dict] = []
    for a, b in zip(pivot_idxs[:-1], pivot_idxs[1:]):
        start_price = float(prices[a])
        end_price   = float(prices[b])
        if start_price <= 0:
            continue
        mag = (end_price - start_price) / start_price * 100.0
        leg = {
            "start_date": dates[a],
            "end_date":   dates[b],
            "regime":     classify_leg(mag, threshold_pct),
            "severity":   round(mag, 2),                # signed magnitude
            "n_days":     b - a + 1,                    # trading days inclusive
        }
        legs.append(leg)

    # Write to DB — idempotent: clear existing auto_zigzag rows for this reference
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "DELETE FROM regimes WHERE reference_index = ? AND source = ?",
            (reference, SOURCE_TAG),
        )
        for leg in legs:
            conn.execute(
                "INSERT INTO regimes "
                "(start_date, end_date, regime, severity, reference_index, notes, source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    leg["start_date"].isoformat(),
                    leg["end_date"].isoformat(),
                    leg["regime"],
                    leg["severity"],
                    reference,
                    f"{leg['n_days']} trading days",
                    SOURCE_TAG,
                ),
            )
        conn.commit()

    # Print summary
    label_zh = {"bull": "多頭", "correction": "修正", "mini_bear": "小熊市", "bear": "熊市"}
    print(f"[step6] {reference}: {len(df)} trading days  "
          f"→ {len(legs)} ZigZag legs (threshold {threshold_pct}%)")
    print()
    for leg in legs:
        label = label_zh.get(leg["regime"], leg["regime"])
        sign  = "+" if leg["severity"] >= 0 else ""
        print(f"  {leg['start_date']} → {leg['end_date']}  "
              f"{leg['regime']:11s}({label})  "
              f"{sign}{leg['severity']:6.2f}%  "
              f"({leg['n_days']} trading days)")
    print()
    by_regime: dict[str, int] = {}
    days_by:   dict[str, int] = {}
    for leg in legs:
        by_regime[leg["regime"]] = by_regime.get(leg["regime"], 0) + 1
        days_by[leg["regime"]]   = days_by.get(leg["regime"], 0) + leg["n_days"]
    print("  Summary by regime:")
    for regime in ("bull", "correction", "mini_bear", "bear"):
        if regime in by_regime:
            print(f"    {regime:11s}: {by_regime[regime]:2d} legs, "
                  f"{days_by[regime]:4d} trading days")
    print()
    print(f"[step6] written to regimes table (source={SOURCE_TAG})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reference", default=DEFAULT_REFERENCE,
                    help=f"Reference ticker in prices table (default: {DEFAULT_REFERENCE})")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD_PCT,
                    help=f"ZigZag reversal threshold in %% (default: {DEFAULT_THRESHOLD_PCT})")
    args = ap.parse_args()
    return run_regimes(reference=args.reference, threshold_pct=args.threshold)


if __name__ == "__main__":
    sys.exit(main())
