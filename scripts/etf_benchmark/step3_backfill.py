"""Step 3 — backfill prices / dividends / splits into SQLite via yfinance.

Strategy:
    • Loads universe.csv (built by step1)
    • Adds 6 reference indices (TAIEX, OTC, SOX, S&P 500, Nasdaq, Dow Jones)
    • For each ticker:
        - First run: yfinance from data_start_date → today (full history)
        - Daily run: yfinance period="5d" → only last few trading days (idempotent)
    • Writes raw close, Yahoo's adj_close, OHLCV, dividends, splits to SQLite
    • Skips weekends/holidays automatically — yfinance only returns trading days
    • Idempotent — INSERT OR REPLACE, safe to re-run any time
    • Per-ticker pass/fail logged to ingest_log table

Run:
    python -m scripts.etf_benchmark.step3_backfill                # full backfill
    python -m scripts.etf_benchmark.step3_backfill --incremental  # last 5d only
    python -m scripts.etf_benchmark.step3_backfill --tickers 0050,00878
    python -m scripts.etf_benchmark.step3_backfill --status       # print DB summary
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

DATA_DIR     = ROOT_DIR / "data" / "etf_bench"
DB_PATH      = DATA_DIR / "etf_bench.sqlite"
UNIVERSE_CSV = DATA_DIR / "universe.csv"

from datetime import date as _date
# Rolling 2-year window — must match step1_universe.BENCH_START semantics
_today = _date.today()
BENCH_START = _today.replace(year=_today.year - 2).isoformat()

# Reference indices — added to the etfs table so price queries are uniform.
REFERENCE_INDICES = [
    # (ticker, name, yahoo_symbol)
    ("^TWII",  "加權指數 TAIEX",         "^TWII"),
    ("^TWOII", "櫃買指數 OTC",           "^TWOII"),
    ("^SOX",   "費城半導體 PHLX Semi",   "^SOX"),
    ("^GSPC",  "標普 500 S&P 500",       "^GSPC"),
    ("^IXIC",  "那斯達克 NASDAQ",        "^IXIC"),
    ("^DJI",   "道瓊工業 Dow Jones",     "^DJI"),
]

CHUNK_SIZE = 50          # tickers per yfinance batch
SLEEP_BETWEEN_CHUNKS = 0.5


# ─── universe loaders ────────────────────────────────────────────────────
def load_universe_rows() -> list[dict]:
    if not UNIVERSE_CSV.exists():
        raise FileNotFoundError(f"{UNIVERSE_CSV} not found — run step1 first")
    rows = list(csv.DictReader(open(UNIVERSE_CSV, encoding="utf-8-sig")))
    for r in rows:
        # Derive yahoo_symbol from market if blank
        if not r.get("yahoo_symbol"):
            suffix = ".TW" if r.get("market") == "TWSE" else ".TWO"
            r["yahoo_symbol"] = f"{r['ticker']}{suffix}"
    return rows


def reference_index_rows() -> list[dict]:
    out = []
    for ticker, name, ysym in REFERENCE_INDICES:
        out.append({
            "ticker": ticker, "name": name,
            "market": "INDEX", "fund_type": "index",
            "is_leveraged_inverse": "False",
            "issuer": "", "tracked_index": "",
            "inception_date": BENCH_START, "listing_date": BENCH_START,
            "data_start_date": BENCH_START,
            "category_raw": "reference_index",
            "full_name": name, "en_name": "",
            "units_issued": "",
            "yahoo_symbol": ysym,
            "source": "reference_index",
        })
    return out


# ─── DB helpers ──────────────────────────────────────────────────────────
def upsert_etfs(conn: sqlite3.Connection, rows: list[dict]) -> int:
    sql = """
    INSERT INTO etfs (
        ticker, name, market, fund_type, is_leveraged_inverse,
        issuer, tracked_index, inception_date, listing_date,
        data_start_date, category_raw, full_name, en_name,
        units_issued, yahoo_symbol, source, updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
    ON CONFLICT(ticker) DO UPDATE SET
        name=excluded.name,
        market=excluded.market,
        fund_type=excluded.fund_type,
        is_leveraged_inverse=excluded.is_leveraged_inverse,
        issuer=excluded.issuer,
        tracked_index=excluded.tracked_index,
        inception_date=excluded.inception_date,
        listing_date=excluded.listing_date,
        data_start_date=excluded.data_start_date,
        category_raw=excluded.category_raw,
        full_name=excluded.full_name,
        en_name=excluded.en_name,
        units_issued=excluded.units_issued,
        yahoo_symbol=excluded.yahoo_symbol,
        source=excluded.source,
        updated_at=datetime('now')
    """
    n = 0
    for r in rows:
        lev = 1 if str(r.get("is_leveraged_inverse", "False")).lower() == "true" else 0
        conn.execute(sql, (
            r["ticker"], r["name"], r["market"], r["fund_type"], lev,
            r.get("issuer", ""), r.get("tracked_index", ""),
            r.get("inception_date") or None, r.get("listing_date") or None,
            r.get("data_start_date") or BENCH_START,
            r.get("category_raw", ""), r.get("full_name", ""), r.get("en_name", ""),
            r.get("units_issued", ""), r.get("yahoo_symbol", ""), r.get("source", ""),
        ))
        n += 1
    conn.commit()
    return n


def log_ingest(conn, ticker, source, table, rows_in, rows_new, status, notes=""):
    conn.execute(
        "INSERT INTO ingest_log (ticker, source, table_name, rows_in, rows_new, status, notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (ticker, source, table, rows_in, rows_new, status, notes),
    )


# ─── yfinance fetch ──────────────────────────────────────────────────────
def fetch_history_batch(ticker_to_symbol: dict[str, str], period: str | None, start: str | None):
    """Returns {ticker: pd.DataFrame with OHLCV+Dividends+Stock Splits} for the batch.
    Missing tickers map to empty DataFrame.
    """
    import yfinance as yf
    symbols = list(ticker_to_symbol.values())
    if not symbols:
        return {}
    kwargs = dict(
        tickers=symbols,
        interval="1d",
        group_by="ticker",
        progress=False,
        threads=True,
        auto_adjust=False,
        actions=True,
    )
    if period:
        kwargs["period"] = period
    else:
        kwargs["start"] = start

    try:
        data = yf.download(**kwargs)
    except Exception as e:
        print(f"  [batch error] {e}")
        return {t: pd.DataFrame() for t in ticker_to_symbol}

    out: dict[str, pd.DataFrame] = {}
    if data is None or data.empty:
        return {t: pd.DataFrame() for t in ticker_to_symbol}

    for ticker, sym in ticker_to_symbol.items():
        try:
            if len(symbols) == 1:
                df = data.copy()
            else:
                df = data[sym].copy()
            df = df.dropna(how="all")
            out[ticker] = df
        except Exception:
            out[ticker] = pd.DataFrame()
    return out


def write_prices(conn, ticker: str, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    sql = """
    INSERT INTO prices (ticker, date, open, high, low, close, adj_close, volume, source, fetched_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'yahoo', datetime('now'))
    ON CONFLICT(ticker, date) DO UPDATE SET
        open=excluded.open, high=excluded.high, low=excluded.low,
        close=excluded.close, adj_close=excluded.adj_close,
        volume=excluded.volume, source=excluded.source,
        fetched_at=datetime('now')
    """
    n = 0
    for idx, row in df.iterrows():
        close = row.get("Close")
        if pd.isna(close):
            continue
        date_iso = idx.date().isoformat()
        conn.execute(sql, (
            ticker, date_iso,
            None if pd.isna(row.get("Open"))      else float(row["Open"]),
            None if pd.isna(row.get("High"))      else float(row["High"]),
            None if pd.isna(row.get("Low"))       else float(row["Low"]),
            float(close),
            None if pd.isna(row.get("Adj Close")) else float(row["Adj Close"]),
            None if pd.isna(row.get("Volume"))    else int(row["Volume"]),
        ))
        n += 1
    return n


def write_dividends(conn, ticker: str, df: pd.DataFrame) -> int:
    if df.empty or "Dividends" not in df.columns:
        return 0
    divs = df["Dividends"].dropna()
    divs = divs[divs > 0]
    if divs.empty:
        return 0
    sql = """
    INSERT INTO dividends (ticker, ex_date, amount, is_income_equalization, source, fetched_at)
    VALUES (?, ?, ?, 0, 'yahoo', datetime('now'))
    ON CONFLICT(ticker, ex_date) DO UPDATE SET
        amount=excluded.amount, fetched_at=datetime('now')
    """
    n = 0
    for ex_date, amount in divs.items():
        conn.execute(sql, (ticker, ex_date.date().isoformat(), float(amount)))
        n += 1
    return n


def write_splits(conn, ticker: str, df: pd.DataFrame) -> int:
    if df.empty or "Stock Splits" not in df.columns:
        return 0
    splits = df["Stock Splits"].dropna()
    splits = splits[splits > 0]
    if splits.empty:
        return 0
    sql = """
    INSERT INTO splits (ticker, ex_date, ratio, source, fetched_at)
    VALUES (?, ?, ?, 'yahoo', datetime('now'))
    ON CONFLICT(ticker, ex_date) DO UPDATE SET
        ratio=excluded.ratio, fetched_at=datetime('now')
    """
    n = 0
    for ex_date, ratio in splits.items():
        conn.execute(sql, (ticker, ex_date.date().isoformat(), float(ratio)))
        n += 1
    return n


# ─── main backfill ───────────────────────────────────────────────────────
def run_backfill(incremental: bool = False, ticker_filter: list[str] | None = None):
    if not DB_PATH.exists():
        print(f"DB not found at {DB_PATH}. Run step2_schema.py first.")
        return 1

    universe = load_universe_rows() + reference_index_rows()
    if ticker_filter:
        wanted = {t.upper() for t in ticker_filter}
        universe = [r for r in universe if r["ticker"].upper() in wanted]
        print(f"[step3] filtered to {len(universe)} tickers: {sorted(wanted)}")

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA foreign_keys = OFF")  # off so reference index rows don't FK-fail
        n_etfs = upsert_etfs(conn, universe)
        print(f"[step3] upserted {n_etfs} etfs (incl. 6 reference indices)")

        # Chunk
        chunks = [universe[i:i+CHUNK_SIZE] for i in range(0, len(universe), CHUNK_SIZE)]
        t_start = time.time()
        total_prices = total_divs = total_splits = 0
        n_ok = n_empty = n_fail = 0
        empty_tickers: list[str] = []

        for ci, chunk in enumerate(chunks, 1):
            t2s = {r["ticker"]: r["yahoo_symbol"] for r in chunk}
            print(f"[step3] chunk {ci}/{len(chunks)} — fetching {len(chunk)} tickers …", flush=True)

            if incremental:
                results = fetch_history_batch(t2s, period="5d", start=None)
            else:
                # Use the earliest data_start_date in this chunk as the batch start.
                # yfinance will return only what each ticker actually has.
                start = min(r.get("data_start_date") or BENCH_START for r in chunk)
                results = fetch_history_batch(t2s, period=None, start=start)

            for r in chunk:
                ticker = r["ticker"]
                df = results.get(ticker, pd.DataFrame())
                if df is None or df.empty:
                    n_empty += 1
                    empty_tickers.append(ticker)
                    log_ingest(conn, ticker, "yahoo", "prices", 0, 0, "empty",
                               f"yfinance returned empty for {r['yahoo_symbol']}")
                    continue
                try:
                    np = write_prices(conn, ticker, df)
                    nd = write_dividends(conn, ticker, df)
                    ns = write_splits(conn, ticker, df)
                    total_prices += np
                    total_divs   += nd
                    total_splits += ns
                    n_ok += 1
                    log_ingest(conn, ticker, "yahoo", "prices", len(df), np, "ok",
                               f"div={nd}, split={ns}")
                except Exception as e:
                    n_fail += 1
                    log_ingest(conn, ticker, "yahoo", "prices", len(df), 0, "fail", str(e)[:200])

            conn.commit()
            time.sleep(SLEEP_BETWEEN_CHUNKS)

        elapsed = time.time() - t_start

    print()
    print(f"[step3] done in {elapsed:.1f}s")
    print(f"  OK    : {n_ok}")
    print(f"  EMPTY : {n_empty}   (Yahoo returned no data)")
    print(f"  FAIL  : {n_fail}")
    print(f"  rows: prices={total_prices:,}  dividends={total_divs}  splits={total_splits}")
    if empty_tickers:
        more = " ..." if len(empty_tickers) > 30 else ""
        print(f"  empty tickers: {', '.join(empty_tickers[:30])}{more}")
    return 0


def print_status():
    if not DB_PATH.exists():
        print(f"DB not found at {DB_PATH}")
        return
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.execute
        n_etfs    = c("SELECT COUNT(*) FROM etfs").fetchone()[0]
        n_prices  = c("SELECT COUNT(*) FROM prices").fetchone()[0]
        n_divs    = c("SELECT COUNT(*) FROM dividends").fetchone()[0]
        n_splits  = c("SELECT COUNT(*) FROM splits").fetchone()[0]
        n_tickers_with_data = c("SELECT COUNT(DISTINCT ticker) FROM prices").fetchone()[0]
        date_min, date_max = c("SELECT MIN(date), MAX(date) FROM prices").fetchone()
        last_run = c("SELECT MAX(run_at) FROM ingest_log").fetchone()[0]
        n_empty   = c("SELECT COUNT(*) FROM ingest_log WHERE status='empty' AND run_at = (SELECT MAX(run_at) FROM ingest_log WHERE ticker=ingest_log.ticker)").fetchone()[0]

    print("=" * 60)
    print(f"DB: {DB_PATH}")
    print(f"  etfs (incl. indices) : {n_etfs}")
    print(f"  tickers with prices  : {n_tickers_with_data}")
    print(f"  price rows           : {n_prices:,}")
    print(f"  dividend rows        : {n_divs}")
    print(f"  split rows           : {n_splits}")
    print(f"  date range           : {date_min} → {date_max}")
    print(f"  last ingest run      : {last_run}")
    print(f"  empty (no Yahoo data): {n_empty}")
    print("=" * 60)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--incremental", action="store_true",
                    help="Only fetch last 5 trading days (for daily refresh)")
    ap.add_argument("--tickers", type=str, default=None,
                    help="Comma-separated tickers to limit the run (default: all)")
    ap.add_argument("--status", action="store_true",
                    help="Print DB summary and exit")
    args = ap.parse_args()

    if args.status:
        print_status()
        return 0

    ticker_filter = [t.strip().upper() for t in (args.tickers or "").split(",") if t.strip()] or None
    return run_backfill(incremental=args.incremental, ticker_filter=ticker_filter)


if __name__ == "__main__":
    sys.exit(main())
