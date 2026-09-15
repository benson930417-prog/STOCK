"""Read-only helpers for the sole ARM market.db used by Streamlit.

All functions are Streamlit-cached. The cache key is keyed on DB mtime so
when the owner pipeline writes new rows, the next page load picks them up.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
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
DB_PATH = Path(
    os.environ.get(
        "STOCK_GLOBAL_MARKET_DB",
        "/var/lib/stock/market/market.db",
    )
)
REGIME_SOURCE = "auto_zigzag"
SCORE_MODEL_VERSION = "fair_score_v1"


def _market_for_symbol(conn: sqlite3.Connection, symbol: str) -> str | None:
    row = conn.execute(
        """SELECT market FROM instruments WHERE symbol=?
             ORDER BY active DESC,
               CASE market WHEN 'TWSE' THEN 0 WHEN 'TPEX' THEN 1
                 WHEN 'INDEX_TW' THEN 2 WHEN 'EQUITY_US' THEN 10
                 WHEN 'ETF_US' THEN 11 WHEN 'INDEX_US' THEN 12
                 ELSE 99 END,
               market
             LIMIT 1""",
        (symbol,),
    ).fetchone()
    if row:
        return str(row["market"])
    fallback = conn.execute(
        """SELECT market FROM daily_bars WHERE symbol=?
             GROUP BY market ORDER BY MAX(date) DESC,market LIMIT 1""",
        (symbol,),
    ).fetchone()
    return str(fallback["market"]) if fallback else None


def _db_mtime() -> float:
    return DB_PATH.stat().st_mtime if DB_PATH.exists() else 0.0


@contextmanager
def _connect():
    conn = sqlite3.connect(f"file:{DB_PATH.resolve().as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        yield conn
    finally:
        conn.close()


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
    """All ETF + reference index rows from the canonical tables.

    Adds a derived column has_prices = True/False based on whether any
    rows exist in daily_bars for that ticker.
    """
    _ = mtime if mtime is not None else _db_mtime()  # cache buster
    if not DB_PATH.exists():
        return pd.DataFrame()
    with _connect() as conn:
        df = pd.read_sql_query(
            """SELECT symbol AS ticker,name,
                      CASE market WHEN 'TPEX' THEN 'TPEx' ELSE market END AS market,
                      fund_type,is_leveraged_inverse,issuer,tracked_index,
                      inception_date,listing_date,listing_date AS data_start_date,category_raw,
                      full_name,en_name,NULL AS units_issued,symbol AS yahoo_symbol,
                      source,'PRIMARY' AS source_quality
                 FROM etf_master WHERE active=1
                UNION ALL
               SELECT symbol AS ticker,name,market,'reference' AS fund_type,
                      0 AS is_leveraged_inverse,NULL AS issuer,NULL AS tracked_index,
                      NULL AS inception_date,NULL AS listing_date,NULL AS data_start_date,
                      'reference' AS category_raw,name AS full_name,name AS en_name,
                      NULL AS units_issued,yahoo_symbol,source,'PRIMARY' AS source_quality
                 FROM instruments
                WHERE active=1 AND asset_type IN ('index','reference')
                ORDER BY ticker""",
            conn,
        )
        ticker_counts = pd.read_sql_query(
            """SELECT symbol AS ticker,COUNT(*) AS n_prices,
                      MIN(date) AS first_date,MAX(date) AS last_date
                 FROM daily_bars GROUP BY symbol""",
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

    with _connect() as conn:
        market = _market_for_symbol(conn, ticker)
        if not market:
            return pd.DataFrame()
        sql = (
            "SELECT date,open,high,low,close,volume FROM daily_bars "
            "WHERE market=? AND symbol=?"
        )
        params: list = [market, ticker]
        if start:
            sql += " AND date >= ?"
            params.append(pd.Timestamp(start).date().isoformat())
        if end:
            sql += " AND date <= ?"
            params.append(pd.Timestamp(end).date().isoformat())
        sql += " ORDER BY date"
        df = pd.read_sql_query(sql, conn, params=params)
        actions = pd.read_sql_query(
            """SELECT ex_date,action_type,value
                 FROM corporate_actions
                WHERE market=? AND symbol=? ORDER BY ex_date""",
            conn,
            params=[market, ticker],
        )
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    # Build a transparent total-return series from raw canonical prices plus
    # canonical cash/split actions. OHLC remains raw and executable.
    action_map: dict[str, dict[str, float]] = {}
    for row in actions.to_dict("records"):
        day = str(row["ex_date"])
        bucket = action_map.setdefault(day, {"cash": 0.0, "split": 1.0})
        if row["action_type"] == "CASH_DIVIDEND":
            bucket["cash"] += float(row["value"])
        elif row["action_type"] == "SPLIT_RATIO":
            bucket["split"] *= float(row["value"])
    adjusted = [float(df.iloc[0]["close"])]
    for index in range(1, len(df)):
        previous = float(df.iloc[index - 1]["close"])
        current = float(df.iloc[index]["close"])
        event = action_map.get(df.iloc[index]["date"].date().isoformat(), {})
        gross = (current * float(event.get("split", 1.0)) + float(event.get("cash", 0.0))) / previous
        adjusted.append(adjusted[-1] * gross)
    df["adj_close"] = adjusted
    return df


@_cache_data(ttl=600)
def get_dividends(ticker: str, mtime: float | None = None) -> pd.DataFrame:
    _ = mtime if mtime is not None else _db_mtime()
    if not DB_PATH.exists():
        return pd.DataFrame()
    with _connect() as conn:
        market = _market_for_symbol(conn, ticker)
        if not market:
            return pd.DataFrame()
        df = pd.read_sql_query(
            """SELECT ex_date,value AS amount,0 AS is_income_equalization
                 FROM corporate_actions
                WHERE market=? AND symbol=? AND action_type='CASH_DIVIDEND'
                ORDER BY ex_date""",
            conn, params=[market, ticker],
        )
    if not df.empty:
        df["ex_date"] = pd.to_datetime(df["ex_date"])
    return df


@_cache_data(ttl=3600)
def get_avg_turnover_map(mtime: float | None = None) -> dict[str, float]:
    """Compute average daily turnover from the last ~3 months of bars.

    Reads from daily_bars directly and never performs an on-demand network call.
    """
    _ = mtime if mtime is not None else _db_mtime()
    if not DB_PATH.exists():
        return {}
    with _connect() as conn:
        # Use last 65 trading days as a 3-month proxy
        df = pd.read_sql_query("""
            WITH ranked AS (
                SELECT symbol AS ticker,date,close,volume,
                       ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY date DESC) AS rn
                FROM daily_bars
                WHERE volume IS NOT NULL AND close IS NOT NULL
                  AND symbol NOT LIKE '^%'
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
    """Regime periods from step4. Columns: start_date, end_date, regime, severity, notes."""
    _ = mtime if mtime is not None else _db_mtime()
    if not DB_PATH.exists():
        return pd.DataFrame()
    with _connect() as conn:
        df = pd.read_sql_query(
            "SELECT start_date,end_date,regime,severity,notes "
            "FROM market_regimes "
            "WHERE reference_symbol=? AND source=? "
            "ORDER BY start_date",
            conn,
            params=[reference_index, REGIME_SOURCE],
        )
    if not df.empty:
        df["start_date"] = pd.to_datetime(df["start_date"])
        df["end_date"]   = pd.to_datetime(df["end_date"])
    return df


@_cache_data(ttl=600)
def get_ingest_status(mtime: float | None = None) -> pd.DataFrame:
    """Most-recent canonical ingest status by job."""
    _ = mtime if mtime is not None else _db_mtime()
    if not DB_PATH.exists():
        return pd.DataFrame()
    with _connect() as conn:
        df = pd.read_sql_query("""
            WITH ranked AS (
                SELECT job,finished_at_utc,status,record_count,failure_count,
                       ROW_NUMBER() OVER (
                           PARTITION BY job
                           ORDER BY finished_at_utc DESC,started_at_utc DESC,run_id DESC
                       ) AS rn
                  FROM ingest_runs
            )
            SELECT job AS ticker,finished_at_utc AS last_run,
                   CASE WHEN status='CLEAN' AND failure_count=0
                        THEN 'ok' ELSE 'incomplete' END AS status,
                   record_count AS rows_in,record_count AS rows_new,
                   '' AS notes
              FROM ranked WHERE rn=1 ORDER BY job
        """, conn)
    return df


@_cache_data(ttl=600)
def get_score_history(mtime: float | None = None) -> pd.DataFrame:
    """Daily fair-score pillars per ETF, written by step5_score.

    Columns: date, ticker, asset_class, n_days, eff, asy
    (eff/asy = 效率/不對稱 percentile sub-scores 0-100; asy NaN if no benchmark).
    The website derives the weighted composite from these so weights stay live.
    """
    _ = mtime if mtime is not None else _db_mtime()
    if not DB_PATH.exists():
        return pd.DataFrame()
    with _connect() as conn:
        df = pd.read_sql_query(
            """SELECT date,ticker,asset_class,n_days,
                      efficiency AS eff,asymmetry AS asy,composite AS score,
                      model_version
                 FROM etf_score_history
                WHERE model_version=?
                ORDER BY date,ticker""",
            conn,
            params=[SCORE_MODEL_VERSION],
        )
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
            "n_etfs":     c("SELECT COUNT(*) FROM etf_master").fetchone()[0],
            "n_with_px":  c("SELECT COUNT(DISTINCT market || ':' || symbol) FROM daily_bars").fetchone()[0],
            "n_prices":   c("SELECT COUNT(*) FROM daily_bars").fetchone()[0],
            "n_dividends":c("SELECT COUNT(*) FROM corporate_actions WHERE action_type='CASH_DIVIDEND'").fetchone()[0],
            "n_splits":   c("SELECT COUNT(*) FROM corporate_actions WHERE action_type='SPLIT_RATIO'").fetchone()[0],
            "date_min":   c("SELECT MIN(date) FROM daily_bars").fetchone()[0],
            "date_max":   c("SELECT MAX(date) FROM daily_bars").fetchone()[0],
        }
