from __future__ import annotations

import unittest

from src.etf_intent_v3_cards import render_intent_grid


class EtfIntentV3CardTests(unittest.TestCase):
    def test_v3_has_two_lanes_and_no_hold_language(self) -> None:
        event = {
            "stock_id": "2308",
            "name": "台達電",
            "category": "電源供應器",
            "event_label": "賣後轉買",
            "reason": "403・981 從顯著賣方轉為顯著買方",
            "etf_label": "403・981",
            "evidence_score": 88,
            "estimated_money_yi": 2.5,
            "data_quality_label": "精確單位數",
            "timing_label": "本交易日觸發",
            "age_sessions": 0,
            "signal_date": "2026-07-23",
            "evidence": [
                {
                    "etf_label": "403",
                    "action": "買進",
                    "significance_ratio": 2.2,
                    "raw_delta_shares": 100_000,
                }
            ],
            "flow_trend_20d": [
                {"date": "2026-07-22", "flow": -0.1},
                {"date": "2026-07-23", "flow": 0.2},
            ],
        }
        html = render_intent_grid(
            {"signals": {"buying": [event], "selling": []}}
        )
        self.assertEqual(2, html.count('class="tfv3-lane"'))
        self.assertIn("新買方意圖", html)
        self.assertIn("新賣方意圖", html)
        self.assertIn("賣後轉買", html)
        self.assertIn("2308", html)
        self.assertIn("白框＝本次轉折依據", html)
        self.assertNotIn("續抱參考", html)


if __name__ == "__main__":
    unittest.main()
