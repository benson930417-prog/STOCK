import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src import market_db


class MarketDbReaderTest(unittest.TestCase):
    def test_prices_actions_and_holdings_share_one_database(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "market.db"
            connection = sqlite3.connect(path)
            try:
                connection.executescript(
                    """
                    CREATE TABLE instruments(
                      market TEXT,symbol TEXT,name TEXT,asset_type TEXT,active INTEGER
                    );
                    CREATE TABLE daily_bars(
                      market TEXT,symbol TEXT,date TEXT,open REAL,high REAL,low REAL,
                      close REAL,volume REAL,source TEXT
                    );
                    CREATE TABLE corporate_actions(
                      market TEXT,symbol TEXT,ex_date TEXT,action_type TEXT,value REAL,source TEXT
                    );
                    CREATE TABLE etf_holding_snapshots(
                      snapshot_id TEXT,etf_symbol TEXT,as_of_date TEXT,source TEXT,
                      fetched_at_utc TEXT,row_count INTEGER,total_weight_pct REAL,
                      complete INTEGER,report_json TEXT
                    );
                    CREATE TABLE etf_holdings(
                      snapshot_id TEXT,rank INTEGER,component_symbol TEXT,
                      component_name TEXT,weight_pct REAL,shares REAL,details_json TEXT
                    );
                    """
                )
                connection.execute(
                    "INSERT INTO daily_bars VALUES(?,?,?,?,?,?,?,?,?)",
                    ("TWSE", "0050", "2026-08-13", 50, 51, 49, 50.5, 1000, "YUANTA_SPARK_GETKLINE"),
                )
                connection.executemany(
                    "INSERT INTO instruments VALUES(?,?,?,?,?)",
                    [
                        ("TWSE", "0050", "元大台灣50", "etf", 1),
                        ("TWSE", "2330", "台積電", "stock", 1),
                    ],
                )
                connection.executemany(
                    "INSERT INTO daily_bars VALUES(?,?,?,?,?,?,?,?,?)",
                    [
                        ("TWSE", "0050", "2026-08-12", 49, 50, 48, 50, 900, "YUANTA_SPARK_GETKLINE"),
                        ("TWSE", "2330", "2026-08-12", 900, 910, 890, 900, 2000, "YUANTA_SPARK_GETKLINE"),
                        ("TWSE", "2330", "2026-08-13", 910, 930, 905, 927, 2500, "YUANTA_SPARK_GETKLINE"),
                    ],
                )
                connection.execute(
                    "INSERT INTO corporate_actions VALUES(?,?,?,?,?,?)",
                    ("TWSE", "0050", "2026-08-13", "CASH_DIVIDEND", 1.0, "TWSE_OFFICIAL_ACTIONS"),
                )
                connection.execute(
                    "INSERT INTO etf_holding_snapshots VALUES(?,?,?,?,?,?,?,?,?)",
                    ("s1", "0050", "2026-08-13", "YUANTA_ISSUER", "2026-08-13T10:00:00Z", 1, 60, 1, json.dumps({"meta": {"nav": 50}})),
                )
                connection.execute(
                    "INSERT INTO etf_holdings VALUES(?,?,?,?,?,?,?)",
                    ("s1", 1, "2330", "台積電", 60, 100, "{}"),
                )
                connection.commit()
            finally:
                connection.close()
            original = market_db.DB_PATH
            market_db.DB_PATH = path
            try:
                prices = market_db.load_daily_ohlcv_payload(["0050"])
                closes = market_db.daily_close_map("0050")
                actions = market_db.load_corporate_action_payload(["0050"])
                day, holdings = market_db.latest_holding_payload("0050")
                quotes = market_db.latest_quote_map(["0050.TW", "2330 TW"])
                holding_text = market_db.etf_holding_quote_text("0050")
            finally:
                market_db.DB_PATH = original
            self.assertTrue(prices["complete"])
            self.assertEqual(closes["2026-08-13"], 50.5)
            self.assertEqual(actions["events"]["0050"][0]["cash_dividend"], 1.0)
            self.assertEqual(day, "2026-08-13")
            self.assertEqual(holdings["meta"]["nav"], 50)
            self.assertEqual(holdings["source"], "YUANTA_ISSUER")
            self.assertEqual(holdings["holdings"][0]["id"], "2330")
            self.assertEqual(quotes["0050.TW"]["symbol"], "0050")
            self.assertAlmostEqual(quotes["2330 TW"]["day_change_pct"], 3.0)
            self.assertIn("ARM market.db", holding_text)
            self.assertIn("報價覆蓋權重 60.0%", holding_text)


if __name__ == "__main__":
    unittest.main()
