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
        "event_date": f"2026-07-{20 - age:02d}",
        "confirmation": "structural",
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
            "holding": [
                {
                    **_event(index + 10),
                    "event_type": "conviction_buy",
                    "event_label": "持續加碼",
                    "buy_evidence_dates": [
                        "2026-07-11",
                        "2026-07-14",
                        "2026-07-18",
                    ],
                }
                for index in range(4)
            ],
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
            "holding": [
                {
                    **_event(index + 10),
                    "event_type": "conviction_buy",
                    "event_label": "持續加碼",
                    "buy_evidence_dates": [
                        "2026-07-11",
                        "2026-07-14",
                        "2026-07-18",
                    ],
                }
                for index in range(4)
            ],
            "selling": [_event(index + 20, age=1) for index in range(4)],
        }

        payload = {"as_of": "2026-07-22", "signals": signals}
        buy = render_lane_html(payload, "buying")
        hold = render_lane_html(payload, "holding")
        sell = render_lane_html(payload, "selling")

        self.assertEqual(6, buy.count('class="tfv2-card tfv2-buy"'))
        self.assertEqual(4, hold.count('class="tfv2-card tfv2-hold"'))
        self.assertEqual(4, sell.count('class="tfv2-card tfv2-sell"'))
        self.assertIn('class="tfv2-age tfv2-age-current">今日仍確認', buy)
        self.assertIn('class="tfv2-age tfv2-age-prior">昨日確認', buy)
        self.assertIn('class="tfv2-age tfv2-age-latest">最新狀態', hold)
        self.assertIn("20日規模比淨動作", buy)
        self.assertIn('class="tfv2-flow-chart"', buy)
        self.assertIn("規模比合計</title>", buy)
        self.assertIn('<span>20日前</span><span>最新</span>', buy)
        self.assertIn("tfv2-flow-structural-dot", buy)
        self.assertIn("持股名單改變 07/20", buy)
        self.assertIn("持股名單改變 07/19", buy)
        self.assertIn('class="tfv2-flow-evidence-buy"', hold)
        self.assertIn('class="tfv2-flow-evidence-latest"', hold)
        self.assertIn("最近一次納入續抱判斷的顯著加碼 07/18", hold)
        self.assertNotIn("tfv2-flow-hold-window", hold)
        self.assertNotIn("tfv2-flow-frame-prior", buy)
        self.assertNotIn("tfv2-flow-frame-current", buy)
        self.assertNotIn("早 ←", buy)
        self.assertIn("測試股票0", buy)
        self.assertNotIn("測試股票10", buy)
        self.assertIn("測試股票13", hold)
        self.assertIn("測試股票23", sell)
        for output in (buy, hold, sell):
            self.assertNotIn("MASTER WU", output)
            self.assertNotIn("判定規則", output)
            self.assertNotIn("max-height", output)
            self.assertNotIn("overflow:hidden", output)

    def test_strategy_specific_flow_markers(self) -> None:
        reversal = _event(40)
        reversal.update(
            event_type="buy_to_sell",
            confirmation="persistence",
            event_label="買後轉賣",
        )
        restart = _event(41)
        restart.update(event_type="restart_buy", event_label="沉寂後開買")

        reversal_html = render_lane_html(
            {"signals": {"selling": [reversal]}}, "selling"
        )
        restart_html = render_lane_html(
            {"signals": {"buying": [restart]}}, "buying"
        )

        self.assertIn("tfv2-flow-frame-reversal", reversal_html)
        self.assertIn("連續兩日反轉確認", reversal_html)
        self.assertIn("tfv2-flow-frame-restart", restart_html)
        self.assertIn("沉寂後重啟確認", restart_html)

    def test_hold_marks_actual_evidence_not_empty_latest_session(self) -> None:
        hold = {
            **_event(50),
            "event_type": "conviction_downgrade",
            "event_label": "續抱降溫",
            "latest_action_label": "尚無顯著賣出",
            "buy_evidence_dates": [
                "2026-07-11",
                "2026-07-14",
                "2026-07-18",
            ],
            "sell_evidence_dates": [],
        }
        hold["flow_trend_20d"][-1]["flow"] = 0
        html = render_lane_html(
            {"signals": {"holding": [hold]}}, "holding"
        )

        self.assertIn("最近一次納入續抱判斷的顯著加碼 07/18", html)
        self.assertNotIn("最近一次納入續抱判斷的顯著加碼 07/20", html)
        self.assertNotIn("最新續抱狀態", html)
        self.assertNotIn("最近10日續抱觀察區", html)


if __name__ == "__main__":
    unittest.main()
