from __future__ import annotations

import unittest

from scripts.generate_etf_action_insight import _selected
from scripts.generate_etf_action_summary import render_html


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

    def test_image_html_contains_the_complete_three_lane_board(self) -> None:
        signals = {
            "buying": [_event(index) for index in range(6)],
            "holding": [_event(index + 10) for index in range(4)],
            "selling": [_event(index + 20, age=1) for index in range(4)],
        }

        output = render_html({"as_of": "2026-07-22", "signals": signals})

        self.assertEqual(14, output.count('class="tfv2-card tfv2-'))
        self.assertIn("本次完整看板：</b>買進 6 檔・續抱 4 檔・賣出 4 檔", output)
        self.assertIn("測試股票0", output)
        self.assertIn("測試股票23", output)
        self.assertNotIn("max-height", output)
        self.assertNotIn("overflow:hidden", output)


if __name__ == "__main__":
    unittest.main()
