from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from scripts.etf_benchmark import db


class BenchmarkDbFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self._tmp.name) / "market.db"
        self._original_db_path = db.DB_PATH
        db.DB_PATH = self.db_path
        with sqlite3.connect(self.db_path) as connection:
            connection.executescript(
                """
                CREATE TABLE market_regimes (
                    reference_symbol TEXT, start_date TEXT, end_date TEXT,
                    regime TEXT, severity REAL, notes TEXT, source TEXT,
                    updated_at_utc TEXT,
                    PRIMARY KEY(reference_symbol,start_date,end_date,source)
                );
                CREATE TABLE ingest_runs (
                    run_id TEXT PRIMARY KEY, job TEXT, host TEXT, trading_date TEXT,
                    package_id TEXT, started_at_utc TEXT, finished_at_utc TEXT,
                    status TEXT, record_count INTEGER, failure_count INTEGER,
                    report_json TEXT
                );
                CREATE TABLE etf_score_history (
                    date TEXT, ticker TEXT, asset_class TEXT, n_days INTEGER,
                    efficiency REAL, asymmetry REAL, composite REAL,
                    model_version TEXT, updated_at_utc TEXT,
                    PRIMARY KEY(date,ticker,model_version)
                );
                """
            )
            connection.executemany(
                "INSERT INTO market_regimes VALUES(?,?,?,?,?,?,?,?)",
                [
                    ("^TWII", "2026-01-01", "2026-01-02", "bull", 1, "", "auto_zigzag", "z"),
                    ("^TWII", "2026-01-03", "2026-01-04", "bear", -1, "", "manual", "z"),
                ],
            )
            connection.executemany(
                "INSERT INTO ingest_runs VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                [
                    ("old", "TAIWAN", "h", "2026-08-20", None, "2026-08-20T01:00:00Z", "2026-08-20T02:00:00Z", "FAILED", 999, 1, "{}"),
                    ("new", "TAIWAN", "h", "2026-08-21", None, "2026-08-21T01:00:00Z", "2026-08-21T02:00:00Z", "CLEAN", 123, 0, "{}"),
                ],
            )
            connection.executemany(
                "INSERT INTO etf_score_history VALUES(?,?,?,?,?,?,?,?,?)",
                [
                    ("2026-08-21", "0050", "equity", 100, 60, 70, None, "fair_score_v1", "z"),
                    ("2026-08-21", "0050", "equity", 100, 1, 2, None, "experimental", "z"),
                ],
            )
        self._clear_caches()

    def tearDown(self) -> None:
        self._clear_caches()
        db.DB_PATH = self._original_db_path
        self._tmp.cleanup()

    @staticmethod
    def _clear_caches() -> None:
        for fn in (db.get_regimes, db.get_ingest_status, db.get_score_history):
            clear = getattr(fn, "clear", None)
            if clear:
                clear()

    def test_only_owned_regime_source_is_visible(self) -> None:
        frame = db.get_regimes(mtime=self.db_path.stat().st_mtime)
        self.assertEqual(["bull"], frame["regime"].tolist())

    def test_ingest_status_uses_latest_run_not_all_history(self) -> None:
        frame = db.get_ingest_status(mtime=self.db_path.stat().st_mtime)
        row = frame.set_index("ticker").loc["TAIWAN"]
        self.assertEqual("ok", row["status"])
        self.assertEqual(123, row["rows_in"])
        self.assertEqual("2026-08-21T02:00:00Z", row["last_run"])

    def test_only_production_score_model_is_visible(self) -> None:
        frame = db.get_score_history(mtime=self.db_path.stat().st_mtime)
        self.assertEqual(["fair_score_v1"], frame["model_version"].tolist())
        self.assertEqual([60.0], frame["eff"].tolist())


if __name__ == "__main__":
    unittest.main()
