"""Read-only helpers for the etf_bench SQLite. Used by the Streamlit app.

All functions are Streamlit-cached. The cache key is keyed on DB mtime so
when the backfill writes new rows, the next page load picks them up.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path

import pandas as pd

# Don't import streamlit at module-top — this file is also used by CLI tools.
try:
    import streamlit as st
    _HAS_STREAMLIT = True
except Exception:
    _HAS_STREAMLIT = False


ROOT_DIR = Path(__file__).resolve().parents[2]
DB_PATH  = ROOT_DIR / "data" / "etf_bench" / "etf_bench.sqlite"
SCORE_HISTORY_CSV = ROOT_DIR / "data" / "etf_bench" / "score_history.csv"


def _db_mtime() -> float:
    return DB_PATH.stat().st_mtime if DB_PATH.exists() else 0.0


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _cache_data(ttl=600):
    """Decorator that uses st.cache_data if available, otherwise a no-op."""
    def deco(fn):
        if not _HAS_STREAMLIT:
            return fn
        return st.cache_data(ttl=ttl, show_spinner=False)(fn)
    return deco


# ───────────────────────────── universe ─────────────────────────────
@_cache_data(ttl=600)
def get_universe(mtime: float | None = None) -> pd.DataFrame:
    """All ETF + reference index rows from the etfs table.

    Adds a derived column has_prices = True/False based on whether any
    rows exist in the prices table for that ticker.
    """
    _ = mtime if mtime is not None else _db_mtime()  # cache buster
    if not DB_PATH.exists():
        return pd.DataFrame()
    with _connect() as conn:
        df = pd.read_sql_query("SELECT * FROM etfs ORDER BY ticker", conn)
        # Derive has_prices via subquery
        ticker_counts = pd.read_sql_query(
            "SELECT ticker, COUNT(*) AS n_prices, MIN(date) AS first_date, MAX(date) AS last_date "
            "FROM prices GROUP BY ticker",
            conn,
        )
    df = df.merge(ticker_counts, on="ticker", how="left")
    df["n_prices"] = df["n_prices"].fillna(0).astype(int)
    df["has_prices"] = df["n_prices"] > 0
    return df


@_cache_data(ttl=600)
def get_prices(
    ticker: str,
    start: str | date | None = None,
    end: str | date | None = None,
    mtime: float | None = None,
) -> pd.DataFrame:
    """Daily OHLCV + adj_close for one ticker. Returns date as datetime64."""
    _ = mtime if mtime is not None else _db_mtime()
    if not DB_PATH.exists():
        return pd.DataFrame()

    sql = "SELECT date, open, high, low, close, adj_close, volume FROM prices WHERE ticker = ?"
    params: list = [ticker]
    if start:
        sql += " AND date >= ?"
        params.append(pd.Timestamp(start).date().isoformat())
    if end:
        sql += " AND date <= ?"
        params.append(pd.Timestamp(end).date().isoformat())
    sql += " ORDER BY date"

    with _connect() as conn:
        df = pd.read_sql_query(sql, conn, params=params)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    return df


@_cache_data(ttl=600)
def get_dividends(ticker: str, mtime: float | None = None) -> pd.DataFrame:
    _ = mtime if mtime is not None else _db_mtime()
    if not DB_PATH.exists():
        return pd.DataFrame()
    with _connect() as conn:
        df = pd.read_sql_query(
            "SELECT ex_date, amount, is_income_equalization FROM dividends "
            "WHERE ticker = ? ORDER BY ex_date",
            conn, params=[ticker],
        )
    if not df.empty:
        df["ex_date"] = pd.to_datetime(df["ex_date"])
    return df


@_cache_data(ttl=3600)
def get_avg_turnover_map(mtime: float | None = None) -> dict[str, float]:
    """Compute avg daily NTD turnover from the last ~3 months of prices.

    Reads from prices table directly (no Yahoo call) — instant.
    """
    _ = mtime if mtime is not None else _db_mtime()
    if not DB_PATH.exists():
        return {}
    with _connect() as conn:
        # Use last 65 trading days as a 3-month proxy
        df = pd.read_sql_query("""
            WITH ranked AS (
                SELECT ticker, date, close, volume,
                       ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY date DESC) AS rn
                FROM prices
                WHERE volume IS NOT NULL AND close IS NOT NULL
                  AND ticker NOT LIKE '^%'        -- exclude indices (volume is not real shares)
            )
            SELECT ticker, AVG(close * volume) AS avg_turnover
            FROM ranked
            WHERE rn <= 65
            GROUP BY ticker
        """, conn)
    return {r["ticker"]: float(r["avg_turnover"] or 0.0) for _, r in df.iterrows()}


@_cache_data(ttl=600)
def get_regimes(
    reference_index: str = "^TWII",
    mtime: float | None = None,
) -> pd.DataFrame:
    """Regime periods from step6. Columns: start_date, end_date, regime, severity, notes."""
    _ = mtime if mtime is not None else _db_mtime()
    if not DB_PATH.exists():
        return pd.DataFrame()
    with _connect() as conn:
        df = pd.read_sql_query(
            "SELECT start_date, end_date, regime, severity, notes "
            "FROM regimes "
            "WHERE reference_index = ? AND source = 'auto_zigzag' "
            "ORDER BY start_date",
            conn,
            params=[reference_index],
        )
    if not df.empty:
        df["start_date"] = pd.to_datetime(df["start_date"])
        df["end_date"]   = pd.to_datetime(df["end_date"])
    return df


@_cache_data(ttl=600)
def get_ingest_status(mtime: float | None = None) -> pd.DataFrame:
    """Most-recent ingest_log row per ticker. Useful for showing 'last refreshed'."""
    _ = mtime if mtime is not None else _db_mtime()
    if not DB_PATH.exists():
        return pd.DataFrame()
    with _connect() as conn:
        df = pd.read_sql_query("""
            SELECT ticker, MAX(run_at) AS last_run, status, rows_in, rows_new, notes
            FROM ingest_log
            GROUP BY ticker
        """, conn)
    return df


@_cache_data(ttl=600)
def get_score_history(mtime: float | None = None) -> pd.DataFrame:
    """Daily fair-score pillars per ETF, written by step7_score.

    Columns: date, ticker, asset_class, n_days, eff, asy, con
    (eff/asy/con = 效率/不對稱/一致性 percentile sub-scores 0-100; NaN if unavailable).
    The website derives the weighted composite from these so weights stay live.
    """
    _ = mtime if mtime is not None else (
        SCORE_HISTORY_CSV.stat().st_mtime if SCORE_HISTORY_CSV.exists() else 0.0
    )
    if not SCORE_HISTORY_CSV.exists():
        return pd.DataFrame()
    df = pd.read_csv(SCORE_HISTORY_CSV)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


def db_summary() -> dict:
    """Quick dict for status badges / debug panel."""
    if not DB_PATH.exists():
        return {"db_exists": False}
    db_mtime = _db_mtime()
    with _connect() as conn:
        c = conn.execute
        return {
            "db_exists":  True,
            "db_path":    str(DB_PATH),
            "db_mtime":   datetime.fromtimestamp(db_mtime).isoformat(timespec="seconds"),
            "db_mtime_epoch": db_mtime,
            "n_etfs":     c("SELECT COUNT(*) FROM etfs").fetchone()[0],
            "n_with_px":  c("SELECT COUNT(DISTINCT ticker) FROM prices").fetchone()[0],
            "n_prices":   c("SELECT COUNT(*) FROM prices").fetchone()[0],
            "n_dividends":c("SELECT COUNT(*) FROM dividends").fetchone()[0],
            "n_splits":   c("SELECT COUNT(*) FROM splits").fetchone()[0],
            "date_min":   c("SELECT MIN(date) FROM prices").fetchone()[0],
            "date_max":   c("SELECT MAX(date) FROM prices").fetchone()[0],
        }
