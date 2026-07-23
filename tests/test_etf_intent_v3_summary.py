from __future__ import annotations

import unittest
import json
from pathlib import Path
import tempfile

from scripts.generate_etf_intent_v3_summary import (
    render_lane_html,
    write_html_preview,
)


class EtfIntentV3SummaryTests(unittest.TestCase):
    def test_each_line_image_contains_only_one_complete_lane(self) -> None:
        event = {
            "stock_id": "2308",
            "name": "台達電",
            "category": "電源供應器",
            "event_label": "賣後轉買",
            "reason": "403・981 從賣方轉為買方",
            "etf_label": "403・981",
            "evidence_score": 90,
            "estimated_money_yi": 2.5,
            "data_quality_label": "精確單位數",
            "timing_label": "本交易日觸發",
            "signal_date": "2026-07-23",
            "evidence": [],
            "flow_trend_20d": [],
        }
        payload = {"signals": {"buying": [event], "selling": []}}
        buy = render_lane_html(payload, "buying")
        sell = render_lane_html(payload, "selling")
        self.assertEqual(1, buy.count('class="tfv3-card tfv3-buy"'))
        self.assertNotIn('class="tfv3-card tfv3-sell"', buy)
        self.assertNotIn('class="tfv3-card"', sell)
        self.assertIn("新買方意圖", buy)
        self.assertIn("新賣方意圖", sell)

    def test_html_preview_needs_no_browser_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache.json"
            preview = root / "preview.html"
            cache.write_text(
                json.dumps(
                    {
                        "as_of": "2026-07-23",
                        "signals": {"buying": [], "selling": []},
                    }
                ),
                encoding="utf-8",
            )
            write_html_preview(preview, cache)
            html = preview.read_text(encoding="utf-8")
        self.assertIn("主動 ETF 意圖轉折", html)
        self.assertEqual(2, html.count('class="tfv3-lane"'))


if __name__ == "__main__":
    unittest.main()
