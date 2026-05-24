"""Step 4 — validate Yahoo's adj_close by reconstructing it ourselves.

For every ETF with dividend events, we compute the expected adj_close from
raw close + dividends, then compare to Yahoo's value.

Math (working backwards from "today"):
    Yahoo's adj_close at date t is:
        close[t] × ∏(1 - amount_i / close_pre_div_i)
    over all dividend events i with ex_date > t.

So for each ex-div event:
    - close_pre  = close on the trading day BEFORE the ex-date
    - factor     = 1 - amount / close_pre
    - Multiply close on every date < ex_date by `factor`.
    - At the latest date, no future dividends → adj_close[last] == close[last].

We then compare against Yahoo's actual adj_close stored in DB.
Threshold: relative diff > 0.5% on any date = FAIL.

Writes one row per (ticker, check_name) to verification_log with status:
    pass / warn / fail

Run:
    python -m scripts.etf_benchmark.step4_verify
    python -m scripts.etf_benchmark.step4_verify --tickers 0050,00878
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

FAIL_THRESHOLD = 0.005      # 0.5%
WARN_THRESHOLD = 0.001      # 0.1%


def _load_prices(conn, ticker: str) -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT date, close, adj_close FROM prices WHERE ticker = ? ORDER BY date",
        conn, params=[ticker],
    )
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    return df


def _load_dividends(conn, ticker: str) -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT ex_date, amount FROM dividends WHERE ticker = ? ORDER BY ex_date",
        conn, params=[ticker],
    )
    if df.empty:
        return df
    df["ex_date"] = pd.to_datetime(df["ex_date"])
    return df


def reconstruct_adj_close(prices: pd.DataFrame, dividends: pd.DataFrame) -> pd.Series:
    """Walk dividends latest → earliest, apply back-adjustment factor to
    all dates strictly before the ex_date. Return reconstructed series."""
    expected = prices["close"].astype(float).copy()
    if dividends.empty:
        return expected

    for row in reversed(list(dividends.itertuples(index=False))):
        ex_date = row.ex_date
        amount = float(row.amount)
        # Find close on the trading day BEFORE ex_date
        mask_pre = prices["date"] < ex_date
        if not mask_pre.any():
            continue
        close_pre = float(prices.loc[mask_pre, "close"].iloc[-1])
        if close_pre <= 0:
            continue
        factor = 1.0 - amount / close_pre
        if factor <= 0 or factor > 1.0:
            continue  # garbage event, skip
        # Apply factor to all dates strictly before ex_date
        expected.loc[mask_pre] *= factor

    return expected


def verify_ticker(conn, ticker: str) -> dict:
    prices = _load_prices(conn, ticker)
    if prices.empty:
        return {"ticker": ticker, "status": "skip", "reason": "no prices"}

    divs = _load_dividends(conn, ticker)
    expected = reconstruct_adj_close(prices, divs)
    actual = prices["adj_close"].astype(float)

    # Drop dates where actual is NaN (shouldn't happen but defensive)
    valid = actual.notna() & expected.notna() & (actual > 0)
    if not valid.any():
        return {"ticker": ticker, "status": "skip", "reason": "no adj_close in DB"}

    e = expected[valid].to_numpy()
    a = actual[valid].to_numpy()
    rel_diff = (e - a) / a
    max_abs_rel = float(abs(rel_diff).max())
    max_abs_rel_date = prices.loc[valid, "date"].iloc[int(abs(rel_diff).argmax())].date().isoformat()

    if max_abs_rel > FAIL_THRESHOLD:
        status = "fail"
    elif max_abs_rel > WARN_THRESHOLD:
        status = "warn"
    else:
        status = "pass"

    return {
        "ticker": ticker,
        "status": status,
        "n_divs": int(len(divs)),
        "n_dates": int(valid.sum()),
        "max_abs_rel_pct": round(max_abs_rel * 100.0, 4),
        "max_diff_date": max_abs_rel_date,
    }


def log_verification(conn, check_name: str, results: list[dict]):
    for r in results:
        if r["status"] == "skip":
            continue
        conn.execute(
            "INSERT INTO verification_log "
            "(check_name, ticker, date, expected, actual, delta_pct, status, notes) "
            "VALUES (?, ?, ?, NULL, NULL, ?, ?, ?)",
            (check_name, r["ticker"], r.get("max_diff_date"),
             r.get("max_abs_rel_pct"), r["status"],
             f"n_divs={r.get('n_divs')} n_dates={r.get('n_dates')}"),
        )
    conn.commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", type=str, default=None,
                    help="Comma-separated tickers (default: all)")
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"DB not found at {DB_PATH}")
        return 1

    with sqlite3.connect(DB_PATH) as conn:
        if args.tickers:
            tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        else:
            tickers = [r[0] for r in conn.execute(
                "SELECT DISTINCT ticker FROM prices WHERE ticker NOT LIKE '^%' ORDER BY ticker"
            ).fetchall()]

        results = []
        for ticker in tickers:
            results.append(verify_ticker(conn, ticker))

        log_verification(conn, "adj_close_vs_reconstructed", results)

    # Summary
    by_status = {}
    for r in results:
        by_status.setdefault(r["status"], []).append(r)

    print()
    print(f"[step4] verified {len(results)} tickers")
    print(f"  pass  : {len(by_status.get('pass', []))}")
    print(f"  warn  : {len(by_status.get('warn', []))}   (>0.1% relative diff somewhere)")
    print(f"  fail  : {len(by_status.get('fail', []))}   (>0.5% relative diff somewhere)")
    print(f"  skip  : {len(by_status.get('skip', []))}")

    for status_label, label_str in [("fail", "FAIL"), ("warn", "WARN")]:
        rows = by_status.get(status_label, [])
        if not rows:
            continue
        print()
        print(f"  {label_str} details:")
        for r in sorted(rows, key=lambda r: -r.get("max_abs_rel_pct", 0))[:20]:
            print(f"    {r['ticker']:8s}  max_diff={r['max_abs_rel_pct']:.3f}%  "
                  f"on {r['max_diff_date']}  (n_divs={r['n_divs']}, n_dates={r['n_dates']})")

    print()
    print(f"[step4] full results logged to verification_log table")
    return 0


if __name__ == "__main__":
    sys.exit(main())
