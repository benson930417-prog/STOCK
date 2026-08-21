#!/usr/bin/env python3
"""Read-only production smoke test for the sole ARM market.db."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("/var/lib/stock/market/market.db"))
    parser.add_argument("--require-taiwan", action="store_true")
    parser.add_argument("--require-score-start", default=None)
    parser.add_argument("--require-etf-holdings", action="store_true")
    args = parser.parse_args()
    db_path = args.db.expanduser().resolve()
    if not db_path.is_file():
        raise SystemExit(f"market database does not exist: {db_path}")

    os.environ["STOCK_GLOBAL_MARKET_DB"] = str(db_path)
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from src.market_db import load_holding_history
    from scripts.etf_benchmark import db as website_db

    uri = f"file:{db_path.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=30) as connection:
        integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
        counts = {
            "instruments": connection.execute("SELECT COUNT(*) FROM instruments WHERE active=1").fetchone()[0],
            "taiwan_instruments": connection.execute(
                "SELECT COUNT(*) FROM instruments WHERE active=1 AND market IN ('TWSE','TPEX')"
            ).fetchone()[0],
            "daily_bars": connection.execute("SELECT COUNT(*) FROM daily_bars").fetchone()[0],
            "yuanta_bars": connection.execute(
                "SELECT COUNT(*) FROM daily_bars WHERE source='YUANTA_SPARK_GETKLINE'"
            ).fetchone()[0],
            "holding_snapshots": connection.execute(
                "SELECT COUNT(*) FROM etf_holding_snapshots WHERE complete=1"
            ).fetchone()[0],
        }
        duplicate_bars = connection.execute(
            """SELECT COUNT(*) FROM (
                   SELECT market,symbol,date,COUNT(*) AS n FROM daily_bars
                   GROUP BY market,symbol,date HAVING n>1
               )"""
        ).fetchone()[0]
        null_ohlcv = connection.execute(
            """SELECT COUNT(*) FROM daily_bars
                 WHERE open IS NULL OR high IS NULL OR low IS NULL
                    OR close IS NULL OR volume IS NULL"""
        ).fetchone()[0]
        ambiguous_active_symbols = connection.execute(
            """SELECT COUNT(*) FROM (
                   SELECT symbol FROM instruments WHERE active=1
                   GROUP BY symbol HAVING COUNT(*)>1
               )"""
        ).fetchone()[0]
        invalid_holding_details = connection.execute(
            "SELECT COUNT(*) FROM etf_holdings WHERE json_valid(details_json)=0"
        ).fetchone()[0]
        canonical_detail_conflicts = connection.execute(
            """SELECT COUNT(*) FROM etf_holdings
                 WHERE json_type(details_json,'$.id') IS NOT NULL
                    OR json_type(details_json,'$.name') IS NOT NULL
                    OR json_type(details_json,'$.weight_pct') IS NOT NULL
                    OR json_type(details_json,'$.shares') IS NOT NULL"""
        ).fetchone()[0]
        score_coverage = connection.execute(
            """SELECT COUNT(*),COUNT(DISTINCT date),MIN(date),MAX(date)
                 FROM etf_score_history WHERE model_version='fair_score_v1'"""
        ).fetchone()
        holdings_tickers = {
            str(row[0]) for row in connection.execute(
                "SELECT DISTINCT etf_symbol FROM etf_holding_snapshots WHERE complete=1"
            )
        }
        sources = dict(
            connection.execute(
                "SELECT source,COUNT(*) FROM daily_bars GROUP BY source ORDER BY source"
            ).fetchall()
        )

    taiex = website_db.get_prices("^TWII")
    holdings_0050 = load_holding_history("0050")
    errors: list[str] = []
    if integrity != "ok":
        errors.append(f"quick_check={integrity}")
    if counts["daily_bars"] < 1_000 or taiex.empty:
        errors.append("foreign/reference OHLCV is missing")
    if counts["holding_snapshots"] < 1 or not holdings_0050:
        errors.append("issuer holdings are missing")
    if duplicate_bars:
        errors.append(f"duplicate daily bars={duplicate_bars}")
    if null_ohlcv:
        errors.append(f"daily bars with NULL OHLCV={null_ohlcv}")
    if ambiguous_active_symbols:
        errors.append(f"ambiguous active symbols={ambiguous_active_symbols}")
    if invalid_holding_details:
        errors.append(f"invalid holding details JSON={invalid_holding_details}")
    if canonical_detail_conflicts:
        errors.append(f"holding details override canonical keys={canonical_detail_conflicts}")
    if args.require_taiwan:
        if not 2_100 <= counts["taiwan_instruments"] <= 3_000:
            errors.append(f"implausible Taiwan universe={counts['taiwan_instruments']}")
        if counts["yuanta_bars"] < 1:
            errors.append("Yuanta Taiwan OHLCV is missing")
    if args.require_score_start:
        if not score_coverage[2] or str(score_coverage[2]) > args.require_score_start:
            errors.append(
                f"score history starts at {score_coverage[2]}, expected <= {args.require_score_start}"
            )
    if args.require_etf_holdings:
        required = {
            "00403A", "00981A", "00988A", "00991A", "0050", "0056",
            "00830", "00878", "00891", "00918", "009805", "009820",
        }
        missing_holdings = sorted(required - holdings_tickers)
        if missing_holdings:
            errors.append(f"missing complete ETF holdings={missing_holdings}")

    report = {
        "db": str(db_path),
        "integrity": integrity,
        "counts": counts,
        "sources": sources,
        "taiex_rows_seen_by_website": len(taiex),
        "0050_snapshots_seen_by_website": len(holdings_0050),
        "duplicate_bars": duplicate_bars,
        "null_ohlcv": null_ohlcv,
        "ambiguous_active_symbols": ambiguous_active_symbols,
        "invalid_holding_details": invalid_holding_details,
        "canonical_detail_conflicts": canonical_detail_conflicts,
        "score_history": {
            "rows": score_coverage[0],
            "dates": score_coverage[1],
            "min": score_coverage[2],
            "max": score_coverage[3],
        },
        "holding_tickers": sorted(holdings_tickers),
        "status": "CLEAN" if not errors else "FAILED",
        "errors": errors,
    }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
