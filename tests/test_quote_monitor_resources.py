from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from scripts import monitor_etf_quotes


ROOT = Path(__file__).resolve().parents[1]
TICKERS = (
    "00403A", "0050", "0056", "00830", "00878", "00891",
    "00918", "009805", "00981A", "009820", "00988A", "00991A",
)


class QuoteMonitorResourceTests(unittest.TestCase):
    def test_one_bounded_unit_owns_every_ticker(self) -> None:
        unit = (ROOT / "services" / "stock-quote-monitor.service").read_text(
            encoding="utf-8"
        )
        self.assertEqual(1, unit.count("ExecStart="))
        for ticker in TICKERS:
            self.assertIn(f" {ticker}", unit)
        self.assertIn("Slice=stock-background.slice", unit)
        self.assertIn("MemoryMax=768M", unit)
        self.assertIn("CPUQuota=100%", unit)
        self.assertIn("TasksMax=96", unit)
        self.assertEqual(
            [ROOT / "services" / "stock-quote-monitor.service"],
            sorted((ROOT / "services").glob("stock-quote-monitor*.service")),
        )

    def test_multi_ticker_supervisor_exits_if_one_loop_dies(self) -> None:
        hold = threading.Event()

        def fake_monitor(ticker, *_args):
            if ticker == "0050":
                raise RuntimeError("boom")
            hold.wait(1)

        try:
            with patch.object(monitor_etf_quotes, "monitor", side_effect=fake_monitor):
                with self.assertRaisesRegex(RuntimeError, "0050 quote monitor stopped"):
                    monitor_etf_quotes.monitor_many(["00403A", "0050"], 180, 4, 150)
        finally:
            hold.set()


if __name__ == "__main__":
    unittest.main()
