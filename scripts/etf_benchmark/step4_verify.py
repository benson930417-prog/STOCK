"""Step 4 - verify Yahoo adj_close as a fair ETF total-return series.

This check is designed around how the Streamlit comparison tab is used:
the user chooses a baseline date and compares return from that date to the
latest available close.

For every ticker, we build an independent transparent total-return series from:
    raw close + cash dividends reinvested at the ex-date close

Then we compare each possible baseline-to-latest return against Yahoo's
adj_close baseline-to-latest return. That answers the actual product question:
"If I use Yahoo adj_close, is the dividend adjustment fair enough for ETF
comparison?"

This intentionally replaces the older exact path reconstruction check. Exact
Yahoo adjustment factors are useful for debugging, but they create noisy fails
that do not always matter for current ETF comparison.

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

# Percentage-point drift between Yahoo adj_close return and transparent
# cash-dividend total return.
WARN_ENDPOINT_DRIFT_PCT = 0.50
FAIL_ENDPOINT_DRIFT_PCT = 2.00
DUPLICATE_DIVIDEND_WINDOW_DAYS = 5

# Some ETF splits/capital actions are already reflected in Yahoo's OHLCV series
# but are not exposed as clean Stock Splits events. Keep these tickers in the
# audit, but always surface the caveat in the daily check.
MANUAL_CORPORATE_ACTION_CAVEATS = {
    "0052": "known split/corporate action; Yahoo OHLCV appears adjusted, but split event is not stored in DB",
}


def _load_prices(conn: sqlite3.Connection, ticker: str) -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT date, close, adj_close FROM prices WHERE ticker = ? ORDER BY date",
        conn,
        params=[ticker],
    )
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    return df


def _load_dividends(conn: sqlite3.Connection, ticker: str) -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT ex_date, amount FROM dividends WHERE ticker = ? ORDER BY ex_date",
        conn,
        params=[ticker],
    )
    if df.empty:
        return df
    df["ex_date"] = pd.to_datetime(df["ex_date"])
    return df


def duplicate_dividend_baseline_dates(prices: pd.DataFrame, dividends: pd.DataFrame) -> set[pd.Timestamp]:
    """Return dates that sit inside likely duplicate Yahoo dividend clusters.

    Yahoo occasionally reports the same cash distribution twice a few trading
    days apart. Its adj_close may include both events, but using one of those
    dates as a comparison baseline is contaminated: the user is starting inside
    the adjustment window. Keep the event stream intact, but ignore those
    baseline dates when scoring max drift.
    """
    if dividends.empty:
        return set()

    df = dividends.copy()
    df = df[df["amount"].notna() & (df["amount"].astype(float) > 0)].copy()
    if df.empty:
        return set()

    df["amount_key"] = df["amount"].astype(float).round(6)
    price_dates = list(prices["date"])
    pos_by_date = {pd.Timestamp(d): i for i, d in enumerate(price_dates)}
    duplicate_dates: set[pd.Timestamp] = set()
    for _, group in df.sort_values("ex_date").groupby("amount_key", sort=False):
        cluster: list[pd.Timestamp] = []

        def flush_cluster() -> None:
            if len(cluster) > 1:
                duplicate_dates.update(cluster)

        for _, row in group.iterrows():
            ex_date = pd.Timestamp(row["ex_date"])
            current_pos = pos_by_date.get(ex_date)
            if not cluster:
                cluster = [ex_date]
                continue

            prev_date = cluster[-1]
            prev_pos = pos_by_date.get(prev_date)
            if current_pos is not None and prev_pos is not None:
                close_enough = (current_pos - prev_pos) <= DUPLICATE_DIVIDEND_WINDOW_DAYS
            else:
                close_enough = (ex_date - prev_date).days <= 10

            if close_enough:
                cluster.append(ex_date)
            else:
                flush_cluster()
                cluster = [ex_date]
        flush_cluster()

    return duplicate_dates


def build_cash_reinvested_index(prices: pd.DataFrame, dividends: pd.DataFrame) -> pd.Series:
    """Return a transparent total-return index.

    The model starts with one share. On each ex-dividend date, dividend cash is
    included in that day's value and then reinvested at that day's close.
    """
    if prices.empty:
        return pd.Series(dtype=float)

    dividend_map = {}
    if not dividends.empty:
        dividend_map = {
            pd.Timestamp(row.ex_date): float(row.amount)
            for row in dividends.itertuples(index=False)
            if row.amount and float(row.amount) > 0
        }

    shares = 1.0
    values: list[float] = []
    for row in prices.itertuples(index=False):
        close = float(row.close)
        div = float(dividend_map.get(pd.Timestamp(row.date), 0.0))
        values.append(shares * (close + div))
        if div > 0 and close > 0:
            shares += shares * div / close

    series = pd.Series(values, index=prices.index, dtype=float)
    if series.empty or series.iloc[0] <= 0:
        return pd.Series(dtype=float)
    return series / series.iloc[0] * 100.0


def verify_ticker(conn: sqlite3.Connection, ticker: str) -> dict:
    prices = _load_prices(conn, ticker)
    if prices.empty:
        return {"ticker": ticker, "status": "skip", "reason": "no prices"}

    prices = prices.dropna(subset=["close"]).reset_index(drop=True)
    if len(prices) < 2:
        return {"ticker": ticker, "status": "skip", "reason": "not enough prices"}

    dividends = _load_dividends(conn, ticker)
    duplicate_baselines = duplicate_dividend_baseline_dates(prices, dividends)
    transparent_index = build_cash_reinvested_index(prices, dividends)
    if transparent_index.empty:
        return {"ticker": ticker, "status": "skip", "reason": "cannot build total return index"}

    yahoo_price = prices["adj_close"].fillna(prices["close"]).astype(float)
    valid = yahoo_price.notna() & (yahoo_price > 0) & transparent_index.notna() & (transparent_index > 0)
    if valid.sum() < 2:
        return {"ticker": ticker, "status": "skip", "reason": "not enough adj_close values"}

    y = yahoo_price[valid].reset_index(drop=True)
    t = transparent_index[valid].reset_index(drop=True)
    dates = prices.loc[valid, "date"].reset_index(drop=True)

    yahoo_index = y / y.iloc[0] * 100.0
    endpoint_drift = ((y.iloc[-1] / y) - (t.iloc[-1] / t)) * 100.0
    terminal_drift_pct = float(endpoint_drift.iloc[0])
    score_mask = ~dates.isin(duplicate_baselines)
    scored_endpoint_drift = endpoint_drift[score_mask] if score_mask.any() else endpoint_drift
    max_endpoint_idx = int(scored_endpoint_drift.abs().idxmax())
    max_endpoint_drift_pct = float(endpoint_drift.iloc[max_endpoint_idx])
    max_path_drift_pct = float((yahoo_index - t).abs().max())

    if abs(max_endpoint_drift_pct) > FAIL_ENDPOINT_DRIFT_PCT:
        status = "fail"
    elif abs(max_endpoint_drift_pct) > WARN_ENDPOINT_DRIFT_PCT:
        status = "warn"
    else:
        status = "pass"

    split_caveat = MANUAL_CORPORATE_ACTION_CAVEATS.get(ticker)
    if split_caveat and status == "pass":
        status = "warn"

    return {
        "ticker": ticker,
        "status": status,
        "split_caveat": split_caveat,
        "n_divs": int(len(dividends)),
        "ignored_duplicate_baselines": int(dates.isin(duplicate_baselines).sum()),
        "n_dates": int(valid.sum()),
        "terminal_drift_pct": round(terminal_drift_pct, 4),
        "max_endpoint_drift_pct": round(max_endpoint_drift_pct, 4),
        "max_endpoint_date": dates.iloc[max_endpoint_idx].date().isoformat(),
        "max_path_drift_pct": round(max_path_drift_pct, 4),
    }


def log_verification(conn: sqlite3.Connection, results: list[dict]) -> None:
    for r in results:
        if r["status"] == "skip":
            continue
        notes = (
            f"terminal_drift_pct={r['terminal_drift_pct']} "
            f"max_path_drift_pct={r['max_path_drift_pct']} "
            f"n_divs={r['n_divs']} "
            f"ignored_duplicate_baselines={r.get('ignored_duplicate_baselines', 0)} "
            f"n_dates={r['n_dates']}"
            + (f" caveat={r['split_caveat']}" if r.get("split_caveat") else "")
        )
        conn.execute(
            "INSERT INTO verification_log "
            "(check_name, ticker, date, expected, actual, delta_pct, status, notes) "
            "VALUES (?, ?, ?, NULL, NULL, ?, ?, ?)",
            (
                "adj_close_vs_cash_reinvested_tr",
                r["ticker"],
                r["max_endpoint_date"],
                r["max_endpoint_drift_pct"],
                r["status"],
                notes,
            ),
        )
    conn.commit()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", type=str, default=None, help="Comma-separated tickers (default: all)")
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"DB not found at {DB_PATH}")
        return 1

    with sqlite3.connect(DB_PATH) as conn:
        if args.tickers:
            tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        else:
            tickers = [
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT ticker FROM prices WHERE ticker NOT LIKE '^%' ORDER BY ticker"
                ).fetchall()
            ]

        results = [verify_ticker(conn, ticker) for ticker in tickers]
        log_verification(conn, results)

    by_status: dict[str, list[dict]] = {}
    for r in results:
        by_status.setdefault(r["status"], []).append(r)

    print()
    print(f"[step4] verified {len(results)} tickers against cash-reinvested total return")
    print(f"  pass  : {len(by_status.get('pass', []))}")
    print(f"  warn  : {len(by_status.get('warn', []))}   (>{WARN_ENDPOINT_DRIFT_PCT:.2f} pct-pt endpoint drift)")
    print(f"  fail  : {len(by_status.get('fail', []))}   (>{FAIL_ENDPOINT_DRIFT_PCT:.2f} pct-pt endpoint drift)")
    print(f"  skip  : {len(by_status.get('skip', []))}")

    for status_label, label_str in [("fail", "FAIL"), ("warn", "WARN")]:
        rows = by_status.get(status_label, [])
        if not rows:
            continue
        print()
        print(f"  {label_str} details:")
        for r in sorted(rows, key=lambda item: abs(item.get("max_endpoint_drift_pct", 0)), reverse=True)[:20]:
            print(
                f"    {r['ticker']:8s}  max_endpoint_drift={r['max_endpoint_drift_pct']:+.3f} pct-pt "
                f"from {r['max_endpoint_date']} to latest  "
                f"(terminal={r['terminal_drift_pct']:+.3f}, max_path={r['max_path_drift_pct']:.3f}, "
                f"n_divs={r['n_divs']}, ignored_dup_baselines={r.get('ignored_duplicate_baselines', 0)})"
                + (f"  NOTE: {r['split_caveat']}" if r.get("split_caveat") else "")
            )

    print()
    print("[step4] full results logged to verification_log table")
    return 0


if __name__ == "__main__":
    sys.exit(main())
