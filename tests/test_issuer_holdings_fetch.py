from __future__ import annotations

import json
import io
import os
import sys
import time
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import run_issuer_holdings_fetch as fetch_run

from scripts.run_issuer_holdings_fetch import (
    DEFAULT_TICKERS,
    _paths,
    _sanitize_history,
    _validate_history,
    _validate_fetch_receipt,
)


class IssuerHoldingsFetchTest(unittest.TestCase):
    def test_zero_exit_with_old_history_is_not_a_clean_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root/"scripts").mkdir()
            (root/"data").mkdir()
            (root/"scripts/fetch_passive_0050.py").write_text("# fake child")
            (root/"data/passive_0050_history.json").write_text(json.dumps({
                "2026-09-14": {"holdings": [{"id": str(i)} for i in range(5)]}
            }), encoding="utf-8")
            receipt = root/"data/passive_0050_log.json"
            receipt.write_text(json.dumps({"status": "No Change", "last_checked_utc":
                (datetime.now(timezone.utc)-timedelta(days=1)).isoformat()}), encoding="utf-8")
            os.utime(receipt, (1, 1))
            manifest = root/"manifest.json"
            argv = ["fetch", "--root", str(root), "--manifest", str(manifest),
                    "--log-dir", str(root/"logs"), "--attempts", "1", "0050"]
            with patch.object(sys, "argv", argv), patch.object(fetch_run.subprocess, "run", return_value=SimpleNamespace(returncode=0)), patch.object(fetch_run.os, "uname", return_value=SimpleNamespace(nodename="test-host"), create=True), redirect_stdout(io.StringIO()):
                code = fetch_run.main()
            result = json.loads(manifest.read_text())
            self.assertEqual(2, code)
            self.assertEqual("PARTIAL", result["status"])
            self.assertEqual("FAILED", result["files"][0]["status"])
            self.assertNotIn("sha256", result["files"][0])

    def test_receipt_distinguishes_verified_unchanged_from_no_data(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root/"data").mkdir()
            receipt = root/"data/passive_0050_log.json"
            start = datetime.now(timezone.utc)
            started_ns = time.time_ns()
            for state in ("NO DATA", "Error: unavailable", "Initializing", "No Change", "NEW DATA FOUND"):
                with self.subTest(state=state):
                    receipt.write_text(json.dumps({"status": state, "last_checked_utc": datetime.now(timezone.utc).isoformat()}), encoding="utf-8")
                    if state in ("No Change", "NEW DATA FOUND"):
                        proof = _validate_fetch_receipt(root, "0050", start, started_ns)
                        self.assertEqual(state.upper(), proof["status"])
                        self.assertEqual(64, len(proof["sha256"]))
                    else:
                        with self.assertRaisesRegex(ValueError, "did not confirm"):
                            _validate_fetch_receipt(root, "0050", start, started_ns)

    def test_accepts_latest_real_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "history.json"
            path.write_text(
                json.dumps(
                    {
                        "2026-08-11": {"holdings": [{"id": str(i)} for i in range(5)]},
                        "2026-08-12": {"holdings": [{"id": str(i)} for i in range(7)]},
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                _validate_history(path), {"latest_date": "2026-08-12", "row_count": 7}
            )

    def test_rejects_mocked_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "history.json"
            path.write_text(
                json.dumps(
                    {
                        "2026-08-12": {
                            "meta": {"is_mocked": True},
                            "holdings": [{"id": str(i)} for i in range(5)],
                        }
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "mocked"):
                _validate_history(path)

    def test_sanitizer_keeps_issuer_nav_and_removes_market_price_fields(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "history.json"
            path.write_text(
                json.dumps(
                    {
                        "2026-08-12": {
                            "meta": {
                                "nav": 50.25,
                                "closing_price": 51.0,
                                "premium_discount_pct": 1.49,
                                "nav_history": {
                                    "latest": {
                                        "nav": 50.25,
                                        "closing_price": 51.0,
                                    }
                                },
                            },
                            "holdings": [{"id": str(i)} for i in range(5)],
                        }
                    }
                ),
                encoding="utf-8",
            )

            _sanitize_history(path)

            meta = json.loads(path.read_text(encoding="utf-8"))["2026-08-12"]["meta"]
            self.assertEqual(50.25, meta["nav"])
            self.assertNotIn("closing_price", meta)
            self.assertNotIn("premium_discount_pct", meta)
            self.assertNotIn("closing_price", meta["nav_history"]["latest"])

    def test_fetchers_do_not_persist_or_request_a_second_price_source(self) -> None:
        root = Path(__file__).resolve().parents[1]
        forbidden = (
            "query1.finance.yahoo.com",
            "closing_price",
            "market_price",
            "premium_discount",
        )
        for ticker in DEFAULT_TICKERS:
            fetcher, _ = _paths(root, ticker)
            source = fetcher.read_text(encoding="utf-8")
            for token in forbidden:
                self.assertNotIn(token, source, f"{ticker} contains {token}")


if __name__ == "__main__":
    unittest.main()
