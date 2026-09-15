from contextlib import contextmanager
import hashlib
import importlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


class SummaryInputCaptureTests(unittest.TestCase):
    def test_all_database_reads_finish_before_browser_and_receipt_binds_images(self):
        fake_api = SimpleNamespace(sync_playwright=Mock())
        with patch.dict(sys.modules, {"playwright": SimpleNamespace(), "playwright.sync_api": fake_api}):
            module = importlib.import_module("scripts.generate_etf_summary")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            summaries = root / "summaries"
            manifest = {"trading_date": "2026-09-15", "run_id": "test-run"}
            (root / "holdings-fetch.json").write_text(json.dumps(manifest), encoding="utf-8")
            state = {"locked": False, "reads": 0}
            @contextmanager
            def lock(_):
                state["locked"] = True
                try:
                    yield
                finally:
                    state["locked"] = False
            def read(_):
                self.assertTrue(state["locked"])
                state["reads"] += 1
                return "2026-09-15", {"meta": {"nav": 10}}, "2026-09-14", {"meta": {"nav": 9}}
            def prices(_):
                self.assertTrue(state["locked"])
                return {"2026-09-15": 10}
            page = Mock()
            def screenshot(**kwargs):
                self.assertFalse(state["locked"])
                Path(kwargs["path"]).write_bytes(b"fake jpeg")
            page.locator.return_value.screenshot.side_effect = screenshot
            browser = Mock()
            browser.new_page.return_value = page
            @contextmanager
            def playwright():
                self.assertFalse(state["locked"])
                self.assertEqual(state["reads"], 4)
                yield SimpleNamespace(chromium=SimpleNamespace(launch=lambda **_: browser))
            with patch.object(module, "input_lock", lock), patch.object(module, "load_data", read), \
                 patch.object(module, "daily_close_map", prices), patch.object(module, "market_revision", return_value="revision"), \
                 patch.object(module, "SUMMARY_DIR", str(summaries)), patch.object(module, "sync_playwright", playwright), \
                 patch.object(module, "render_html", return_value="<body>fake</body>"), \
                 patch("scripts.daily_line_publish._validate_import"), \
                 patch.dict("os.environ", {"STOCK_GLOBAL_MARKET_DB": str(root / "market.db")}):
                module.generate()
            receipt = json.loads((summaries / "summary-inputs.json").read_text(encoding="utf-8"))
            self.assertEqual(receipt["fetch_run_id"], "test-run")
            self.assertEqual(receipt["market_revision"], "revision")
            self.assertEqual(len(receipt["histories"]), 4)
            self.assertEqual(set(receipt["images"].values()), {hashlib.sha256(b"fake jpeg").hexdigest()})
            browser.close.assert_called_once()
