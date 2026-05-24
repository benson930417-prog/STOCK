"""Step 2 — initialise the benchmark SQLite database.

Schema goals:
    • RAW prices stored only (close, open, high, low, volume). No adj_close.
      We reconstruct adj_close ourselves from raw + dividends + splits,
      so step5 can validate against Yahoo's adj_close as an independent check.
    • Dividends and splits stored as separate event tables.
    • Total-return index stored in its own table (computed in step4).
    • verification_log keeps every check + its delta — never deleted, so we
      have a full audit trail for "is the DB trustworthy right now?".

Run:
    python -m scripts.etf_benchmark.step2_schema
    python -m scripts.etf_benchmark.step2_schema --reset   # drop + recreate
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "data" / "etf_bench"
DB_PATH  = DATA_DIR / "etf_bench.sqlite"


SCHEMA_SQL = """
-- ────────────────────────────────────────────────────────────────────────
-- ETF master (populated from step1's universe.csv)
-- ────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS etfs (
    ticker                TEXT PRIMARY KEY,
    name                  TEXT NOT NULL,
    market                TEXT NOT NULL,         -- 'TWSE' / 'TPEx' / 'INDEX'
    fund_type             TEXT NOT NULL,         -- passive_equity / active_equity / bond / commodity / leveraged / index / other
    is_leveraged_inverse  INTEGER NOT NULL DEFAULT 0,
    issuer                TEXT,
    tracked_index         TEXT,
    inception_date        TEXT,                  -- ISO YYYY-MM-DD
    listing_date          TEXT,
    data_start_date       TEXT NOT NULL,         -- max(2025-01-01, inception)
    category_raw          TEXT,
    full_name             TEXT,
    en_name               TEXT,
    units_issued          TEXT,
    yahoo_symbol          TEXT,                  -- e.g. '0050.TW' or '^TWII'
    source                TEXT,                  -- 'twse_opendata' / 'tpex_seed' / 'manual' / 'reference_index'
    avg_turnover_3mo      REAL NOT NULL DEFAULT 0,  -- cached daily NTD turnover, refreshed weekly
    avg_turnover_updated  TEXT,
    created_at            TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at            TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_etfs_type ON etfs(fund_type);
CREATE INDEX IF NOT EXISTS idx_etfs_market ON etfs(market);

-- ────────────────────────────────────────────────────────────────────────
-- Daily RAW prices (no adjustment). One row per ticker per trading day.
-- ────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS prices (
    ticker      TEXT NOT NULL,
    date        TEXT NOT NULL,                   -- ISO YYYY-MM-DD
    open        REAL,
    high        REAL,
    low         REAL,
    close       REAL NOT NULL,                   -- raw close
    adj_close   REAL,                            -- Yahoo's dividend-adjusted close (for fair returns)
    volume      INTEGER,
    source      TEXT NOT NULL,                   -- 'yahoo' / 'twse' / 'manual'
    fetched_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (ticker, date),
    FOREIGN KEY (ticker) REFERENCES etfs(ticker)
);
CREATE INDEX IF NOT EXISTS idx_prices_date ON prices(date);

-- ────────────────────────────────────────────────────────────────────────
-- Dividend events. Amount in NTD per share, on the ex-dividend date.
-- is_income_equalization flags 收益平準金 — mathematically same treatment
-- as a real dividend in the TR formula, but tagged so the yield calc can
-- subtract it.
-- ────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dividends (
    ticker                    TEXT NOT NULL,
    ex_date                   TEXT NOT NULL,     -- ISO YYYY-MM-DD
    amount                    REAL NOT NULL,
    is_income_equalization    INTEGER NOT NULL DEFAULT 0,
    source                    TEXT NOT NULL,     -- 'yahoo' / 'twse_announcement' / 'manual'
    notes                     TEXT,
    fetched_at                TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (ticker, ex_date),
    FOREIGN KEY (ticker) REFERENCES etfs(ticker)
);
CREATE INDEX IF NOT EXISTS idx_dividends_date ON dividends(ex_date);

-- ────────────────────────────────────────────────────────────────────────
-- Split / reverse-split events.
-- ratio = new_shares / old_shares
--    e.g.  1股拆2股 → ratio = 2.0
--    e.g.  5股併1股 → ratio = 0.2
-- step4 retroactively divides all earlier prices by ratio (and multiplies
-- earlier volumes / dividends by ratio).
-- ────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS splits (
    ticker      TEXT NOT NULL,
    ex_date     TEXT NOT NULL,
    ratio       REAL NOT NULL,
    source      TEXT NOT NULL,
    notes       TEXT,
    fetched_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (ticker, ex_date),
    FOREIGN KEY (ticker) REFERENCES etfs(ticker)
);

-- ────────────────────────────────────────────────────────────────────────
-- Total-return index per ticker, base = 100 at data_start_date.
-- Recomputed by step4 whenever prices/dividends/splits change.
-- ────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tr_index (
    ticker      TEXT NOT NULL,
    date        TEXT NOT NULL,
    value       REAL NOT NULL,                   -- total-return index value
    computed_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (ticker, date),
    FOREIGN KEY (ticker) REFERENCES etfs(ticker)
);

-- ────────────────────────────────────────────────────────────────────────
-- Benchmark indices (TAIEX price + TAIEX total return).
-- One row per trading date.
-- ────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS benchmark (
    date            TEXT PRIMARY KEY,
    taiex_close     REAL,                        -- ^TWII or MI_5MINS_HIST
    taiex_tr        REAL,                        -- MFI94U (TR index)
    twse50_close    REAL,                        -- TAI50I
    source          TEXT,
    fetched_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ────────────────────────────────────────────────────────────────────────
-- Regime tags (filled by a later script).
-- Period semantics: [start_date, end_date] inclusive.
-- ────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS regimes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    start_date      TEXT NOT NULL,
    end_date        TEXT NOT NULL,
    regime          TEXT NOT NULL,               -- bull / correction / mini_bear / bear
    severity        REAL,                        -- max DD% inside the window
    reference_index TEXT NOT NULL,               -- 'TAIEX_TR' / 'SOX' / etc.
    notes           TEXT,
    source          TEXT NOT NULL,               -- 'auto_drawdown' / 'manual'
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_regimes_range ON regimes(start_date, end_date);

-- ────────────────────────────────────────────────────────────────────────
-- Ingest log — one row per (ticker, run). Tells us when we last hit
-- Yahoo / TWSE for this ticker, how many rows came back, and pass/fail.
-- ────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ingest_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker      TEXT,
    source      TEXT NOT NULL,
    table_name  TEXT NOT NULL,                   -- prices / dividends / splits / benchmark
    run_at      TEXT NOT NULL DEFAULT (datetime('now')),
    rows_in     INTEGER,
    rows_new    INTEGER,
    status      TEXT NOT NULL,                   -- ok / partial / fail
    notes       TEXT
);
CREATE INDEX IF NOT EXISTS idx_ingest_log_ticker ON ingest_log(ticker, run_at);

-- ────────────────────────────────────────────────────────────────────────
-- Verification log — one row per check. Persisted forever.
-- A ticker is considered "trusted" iff its most recent verification
-- of each check_name == 'pass'.
-- ────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS verification_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    check_name  TEXT NOT NULL,                   -- 'adj_close_vs_yahoo' / 'twse_snapshot_match' / 'dividend_cross_check' / 'coverage_gap'
    ticker      TEXT,
    date        TEXT,                            -- the specific date the check refers to, if any
    expected    REAL,
    actual      REAL,
    delta_pct   REAL,
    status      TEXT NOT NULL,                   -- pass / warn / fail
    notes       TEXT,
    run_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_verlog_check_ticker ON verification_log(check_name, ticker, run_at);
CREATE INDEX IF NOT EXISTS idx_verlog_status ON verification_log(status, run_at);
"""


RESET_SQL = """
DROP TABLE IF EXISTS verification_log;
DROP TABLE IF EXISTS ingest_log;
DROP TABLE IF EXISTS regimes;
DROP TABLE IF EXISTS benchmark;
DROP TABLE IF EXISTS tr_index;
DROP TABLE IF EXISTS splits;
DROP TABLE IF EXISTS dividends;
DROP TABLE IF EXISTS prices;
DROP TABLE IF EXISTS etfs;
"""


def init_db(reset: bool = False) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[step2] DB path: {DB_PATH}")

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        if reset:
            print("[step2] --reset: dropping all tables")
            conn.executescript(RESET_SQL)
        conn.executescript(SCHEMA_SQL)
        conn.commit()

        # Verify
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = [r[0] for r in cur.fetchall()]

    print(f"[step2] tables present: {', '.join(tables)}")
    expected = {
        "benchmark", "dividends", "etfs", "ingest_log",
        "prices", "regimes", "splits", "tr_index", "verification_log",
    }
    missing = expected - set(tables)
    if missing:
        print(f"[step2] FAIL: missing tables {missing}")
        sys.exit(1)
    print("[step2] schema OK")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true",
                    help="drop all tables before recreating (destroys data)")
    args = ap.parse_args()
    init_db(reset=args.reset)


if __name__ == "__main__":
    main()
