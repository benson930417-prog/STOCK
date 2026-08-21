from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from src import market_db


class MarketDbAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self._tmp.name) / "market.db"
        self._original_db_path = market_db.DB_PATH
        market_db.DB_PATH = self.db_path
        with sqlite3.connect(self.db_path) as connection:
            connection.executescript(
                """
                CREATE TABLE instruments (
                    market TEXT NOT NULL, symbol TEXT NOT NULL, name TEXT NOT NULL,
                    asset_type TEXT NOT NULL, currency TEXT NOT NULL,
                    yahoo_symbol TEXT, listing_date TEXT, active INTEGER NOT NULL,
                    source TEXT NOT NULL, details_json TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL, PRIMARY KEY (market,symbol)
                );
                CREATE TABLE daily_bars (
                    market TEXT NOT NULL, symbol TEXT NOT NULL, date TEXT NOT NULL,
                    open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
                    close REAL NOT NULL, volume REAL NOT NULL, currency TEXT NOT NULL,
                    source TEXT NOT NULL, package_id TEXT, ingested_at_utc TEXT NOT NULL,
                    PRIMARY KEY (market,symbol,date)
                );
                CREATE TABLE corporate_actions (
                    market TEXT NOT NULL, symbol TEXT NOT NULL, ex_date TEXT NOT NULL,
                    action_type TEXT NOT NULL, value REAL NOT NULL, source TEXT NOT NULL,
                    details_json TEXT NOT NULL, ingested_at_utc TEXT NOT NULL,
                    PRIMARY KEY (market,symbol,ex_date,action_type)
                );
                CREATE TABLE etf_holding_snapshots (
                    snapshot_id TEXT PRIMARY KEY, etf_market TEXT, etf_symbol TEXT,
                    as_of_date TEXT, fetched_at_utc TEXT, source TEXT, complete INTEGER,
                    row_count INTEGER, total_weight_pct REAL, report_json TEXT
                );
                CREATE TABLE etf_holdings (
                    snapshot_id TEXT, rank INTEGER, component_symbol TEXT,
                    component_name TEXT, weight_pct REAL, shares REAL,
                    details_json TEXT
                );
                """
            )

    def tearDown(self) -> None:
        market_db.DB_PATH = self._original_db_path
        self._tmp.cleanup()

    def test_details_json_cannot_override_canonical_holding_fields(self) -> None:
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                "INSERT INTO etf_holding_snapshots VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    "s1", "TWSE", "0050", "2026-08-21", "2026-08-21T10:00:00Z",
                    "issuer", 1, 1, 10.0, json.dumps({"meta": {"nav": 50}}),
                ),
            )
            connection.execute(
                "INSERT INTO etf_holdings VALUES(?,?,?,?,?,?,?)",
                (
                    "s1", 1, "2330", "台積電", 10.0, 123.0,
                    json.dumps(
                        {
                            "id": "ATTACK", "name": "wrong", "weight_pct": 999,
                            "shares": -1, "country": "TW",
                        }
                    ),
                ),
            )

        item = market_db.load_holding_history("0050")["2026-08-21"]["holdings"][0]
        self.assertEqual("2330", item["id"])
        self.assertEqual("台積電", item["name"])
        self.assertEqual(10.0, item["weight_pct"])
        self.assertEqual(123.0, item["shares"])
        self.assertEqual("TW", item["country"])

    def test_symbol_only_contract_never_mixes_markets(self) -> None:
        with sqlite3.connect(self.db_path) as connection:
            for market, name in (("TWSE", "primary"), ("TPEX", "duplicate")):
                connection.execute(
                    "INSERT INTO instruments VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        market, "DUP", name, "stock", "TWD", None, None, 1,
                        "test", "{}", "2026-08-21T00:00:00Z",
                    ),
                )
            for market, closes in (("TWSE", (10.0, 11.0)), ("TPEX", (100.0, 50.0))):
                for day, close in zip(("2026-08-20", "2026-08-21"), closes):
                    connection.execute(
                        "INSERT INTO daily_bars VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            market, "DUP", day, close, close, close, close, 1000,
                            "TWD", "test", None, "2026-08-21T00:00:00Z",
                        ),
                    )
            connection.execute(
                "INSERT INTO corporate_actions VALUES(?,?,?,?,?,?,?,?)",
                (
                    "TWSE", "DUP", "2026-08-21", "CASH_DIVIDEND", 1.0,
                    "test", "{}", "2026-08-21T00:00:00Z",
                ),
            )
            connection.execute(
                "INSERT INTO corporate_actions VALUES(?,?,?,?,?,?,?,?)",
                (
                    "TPEX", "DUP", "2026-08-21", "CASH_DIVIDEND", 99.0,
                    "test", "{}", "2026-08-21T00:00:00Z",
                ),
            )

        self.assertEqual(
            {"2026-08-20": 10.0, "2026-08-21": 11.0},
            market_db.daily_close_map("DUP"),
        )
        quote = market_db.latest_quote_map(["DUP"])["DUP"]
        self.assertEqual("TWSE", quote["market"])
        self.assertEqual(11.0, quote["price"])
        self.assertEqual(10.0, quote["previous_close"])
        payload = market_db.load_daily_ohlcv_payload(["DUP"], benchmark="DUP")
        self.assertEqual([10.0, 11.0], [row["close"] for row in payload["symbols"]["DUP"]])
        actions = market_db.load_corporate_action_payload(["DUP"])
        self.assertEqual(1.0, actions["events"]["DUP"][0]["cash_dividend"])


if __name__ == "__main__":
    unittest.main()
