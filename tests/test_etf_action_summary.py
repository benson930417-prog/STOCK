from __future__ import annotations

import unittest

from scripts.generate_etf_action_insight import _selected
from scripts.generate_etf_action_summary import render_lane_html


def _event(index: int, *, age: int = 0) -> dict:
    return {
        "stock_id": str(2300 + index),
        "name": f"測試股票{index}",
        "category": "測試類股",
        "event_type": "new_position",
        "event_label": "新建倉",
        "age_sessions": age,
        "etf_label": "403・981",
        "qualification_label": "共識（2/3）",
        "evidence_parts": ["測試依據"],
        "flow_trend_20d": [
            {
                "date": f"2026-07-{day:02d}",
                "flow": (0.01 * day) if day % 3 else (-0.008 * day),
                "breadth": 2,
            }
            for day in range(1, 21)
        ],
    }


class EtfActionSummaryTests(unittest.TestCase):
    def test_line_cache_keeps_every_event_already_selected_by_engine(self) -> None:
        snapshot = {
            "buying": [_event(index) for index in range(6)],
            "holding": [_event(index + 10) for index in range(4)],
            "selling": [_event(index + 20, age=1) for index in range(4)],
        }

        selected = _selected(snapshot)

        self.assertEqual(6, len(selected["buying"]))
        self.assertEqual(4, len(selected["holding"]))
        self.assertEqual(4, len(selected["selling"]))

    def test_each_image_html_contains_one_complete_lane_only(self) -> None:
        signals = {
            "buying": [
                *[_event(index) for index in range(5)],
                _event(5, age=1),
            ],
            "holding": [_event(index + 10) for index in range(4)],
            "selling": [_event(index + 20, age=1) for index in range(4)],
        }

        payload = {"as_of": "2026-07-22", "signals": signals}
        buy = render_lane_html(payload, "buying")
        hold = render_lane_html(payload, "holding")
        sell = render_lane_html(payload, "selling")

        self.assertEqual(6, buy.count('class="tfv2-card tfv2-buy"'))
        self.assertEqual(4, hold.count('class="tfv2-card tfv2-hold"'))
        self.assertEqual(4, sell.count('class="tfv2-card tfv2-sell"'))
        self.assertIn('class="tfv2-age tfv2-age-current">本交易日確認', buy)
        self.assertIn('class="tfv2-age tfv2-age-prior">前一交易日確認', buy)
        self.assertIn('class="tfv2-age tfv2-age-latest">最新資料仍確認', hold)
        self.assertIn("20日規模比淨動作", buy)
        self.assertIn('class="tfv2-flow-chart"', buy)
        self.assertIn("規模比合計</title>", buy)
        self.assertIn("測試股票0", buy)
        self.assertNotIn("測試股票10", buy)
        self.assertIn("測試股票13", hold)
        self.assertIn("測試股票23", sell)
        for output in (buy, hold, sell):
            self.assertNotIn("MASTER WU", output)
            self.assertNotIn("判定規則", output)
            self.assertNotIn("max-height", output)
            self.assertNotIn("overflow:hidden", output)


if __name__ == "__main__":
    unittest.main()
