from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from scripts.daily_line_publish import (
    PublicationError,
    _canonical_sha256,
    publish_daily,
    receipt_path,
    upstream_ready_path,
)


DAY = "2026-08-31"


class DailyLinePublicationTests(unittest.TestCase):
    def test_orchestrator_exports_sourced_line_token_to_python(self) -> None:
        script = (
            Path(__file__).resolve().parents[1] / "scripts" / "update_and_notify.sh"
        ).read_text(encoding="utf-8")

        self.assertIn(
            '[ -n "${LINE_TOKEN:-}" ] && export LINE_TOKEN', script
        )
        self.assertIn(
            '[ -n "${LINE_CHANNEL_ACCESS_TOKEN:-}" ] && export LINE_CHANNEL_ACCESS_TOKEN',
            script,
        )

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.market_dir = self.root / "market"
        self.data_dir = self.root / "data"
        (self.data_dir / "summaries").mkdir(parents=True)
        self.market_dir.mkdir(parents=True)
        (self.data_dir / "etf_action_insight.json").write_text(
            json.dumps(
                {
                    "as_of": DAY,
                    "generated": "2026-08-31T13:01:00Z",
                    "line_text": "complete action report",
                }
            ),
            encoding="utf-8",
        )
        image = self.data_dir / "summaries" / "etf_A_summary_latest.jpg"
        image.write_bytes(b"jpeg")
        fresh = datetime(2026, 8, 31, 13, 2, tzinfo=timezone.utc).timestamp()
        os.utime(image, (fresh, fresh))
        self.history = {"A": {DAY: {}}}

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_sealed_inputs(self, *, clean: bool = True) -> None:
        manifest = {
            "schema_version": 1,
            "job": "ETF_ISSUER_FETCH",
            "run_id": "issuer-holdings-test",
            "trading_date": DAY,
            "started_at_utc": "2026-08-31T12:59:00Z",
            "finished_at_utc": "2026-08-31T13:00:00Z",
            "status": "CLEAN" if clean else "PARTIAL_FAIL",
            "failure_count": 0 if clean else 1,
            "ticker_count": 1,
            "files": [
                {
                    "ticker": "A",
                    "status": "CLEAN" if clean else "FAILED",
                    "latest_date": DAY,
                    "row_count": 1 if clean else 0,
                }
            ],
        }
        (self.market_dir / "holdings-fetch.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        if clean:
            path = upstream_ready_path(self.market_dir, DAY)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "trading_date": DAY,
                        "status": "READY",
                        "fetch_run_id": manifest["run_id"],
                    }
                ),
                encoding="utf-8",
            )
        db = self.market_dir / "market.db"
        with closing(sqlite3.connect(db)) as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS ingest_runs (
                       job TEXT, trading_date TEXT, status TEXT,
                       failure_count INTEGER, report_json TEXT,
                       finished_at_utc TEXT
                   )"""
            )
            connection.execute("DELETE FROM ingest_runs")
            if clean:
                report = {
                    "source_run_id": manifest["run_id"],
                    "manifest_sha256": _canonical_sha256(manifest),
                }
                connection.execute(
                    "INSERT INTO ingest_runs VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        "ETF_ISSUER_HOLDINGS",
                        DAY,
                        "CLEAN",
                        0,
                        json.dumps(report),
                        "2026-08-31T13:00:30Z",
                    ),
                )
            connection.commit()

    def publish(self, sender):
        return publish_daily(
            DAY,
            "token",
            market_dir=self.market_dir,
            data_dir=self.data_dir,
            sender=sender,
            expected_tickers=("A",),
            active_tickers=["A"],
            history_loader=lambda ticker: self.history[ticker],
        )

    def test_incomplete_day_stays_pending_and_never_calls_line(self) -> None:
        self.write_sealed_inputs(clean=False)
        calls = []

        with self.assertRaisesRegex(PublicationError, "not CLEAN"):
            self.publish(lambda *args: calls.append(args))

        state = json.loads(
            receipt_path(self.market_dir, DAY).read_text(encoding="utf-8")
        )
        self.assertEqual([], calls)
        self.assertEqual("PENDING", state["status"])
        self.assertIn("INCOMPLETE", state["pending_reason"])

    def test_first_repaired_complete_run_sends_and_later_run_does_not(self) -> None:
        self.write_sealed_inputs(clean=False)
        with self.assertRaises(PublicationError):
            self.publish(lambda *_: (200, "{}"))
        self.write_sealed_inputs(clean=True)
        calls = []

        first = self.publish(
            lambda body, token, retry_key: (
                calls.append((body, token, retry_key)) or (200, "{}")
            )
        )
        second = self.publish(
            lambda *_: self.fail("a SENT day must not contact LINE again")
        )

        self.assertEqual(1, len(calls))
        self.assertEqual("SENT", first["status"])
        self.assertEqual("HTTP_200", first["acceptance"])
        self.assertEqual(first["retry_key"], second["retry_key"])
        self.assertEqual(1, second["attempt_count"])

    def test_retry_reuses_key_and_http_409_closes_uncertain_attempt(self) -> None:
        self.write_sealed_inputs(clean=True)
        first_key = []

        def timeout_sender(_body, _token, retry_key):
            first_key.append(retry_key)
            raise OSError("timeout after request")

        with self.assertRaisesRegex(PublicationError, "timeout after request"):
            self.publish(timeout_sender)

        second_keys = []
        state = self.publish(
            lambda _body, _token, retry_key: (
                second_keys.append(retry_key) or (409, "already accepted")
            )
        )

        self.assertEqual(first_key, second_keys)
        self.assertEqual("SENT", state["status"])
        self.assertEqual("HTTP_409_ALREADY_ACCEPTED", state["acceptance"])
        self.assertEqual(2, state["attempt_count"])

    def test_uncertain_attempt_is_not_replayed_after_retry_window(self) -> None:
        self.write_sealed_inputs(clean=True)

        with self.assertRaises(PublicationError):
            self.publish(lambda *_: (_ for _ in ()).throw(OSError("timeout")))
        path = receipt_path(self.market_dir, DAY)
        state = json.loads(path.read_text(encoding="utf-8"))
        state["first_attempt_at_utc"] = (
            datetime.now(timezone.utc) - timedelta(hours=25)
        ).isoformat().replace("+00:00", "Z")
        path.write_text(json.dumps(state), encoding="utf-8")
        calls = []

        with self.assertRaisesRegex(PublicationError, "retry-key window expired"):
            self.publish(lambda *args: calls.append(args))

        expired = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual([], calls)
        self.assertEqual("UNCERTAIN_EXPIRED", expired["status"])


if __name__ == "__main__":
    unittest.main()
