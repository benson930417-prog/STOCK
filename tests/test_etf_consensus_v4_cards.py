from __future__ import annotations

import unittest

from src.etf_consensus_v4_cards import render_v4_card, render_v4_lane


class EtfConsensusV4CardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.card = {
            "stock_id": "2308",
            "name": "台達電",
            "category": "電源供應器",
            "state": "buy",
            "direction": 1,
            "score": 72,
            "score_label": "共識強度",
            "score_components": {
                "independent_etfs": 25,
                "joint_persistence": 18,
            },
            "transition": "新形成買方共識",
            "participants": ["00403A", "00981A"],
            "etf_label": "403・981",
            "breadth": 2,
            "confirmed_date": "2026-07-23",
            "state_days": 1,
            "evidence": [
                {
                    "etf_label": "403",
                    "direction": 1,
                    "active_flow": 0.15,
                    "significance_ratio": 2.2,
                }
            ],
            "etf_trends": {
                "00403A": [
                    {
                        "date": "2026-07-23",
                        "ratio": 2.2,
                        "active_flow": 0.15,
                        "significance_ratio": 2.2,
                        "significant": True,
                    }
                ],
                "00981A": [],
                "00991A": [],
            },
        }

    def test_card_explains_score_and_own_threshold(self) -> None:
        html = render_v4_card(self.card, "buy")
        self.assertIn("共識強度 72/100", html)
        self.assertIn("2.2×門檻", html)
        self.assertIn("3日窗", html)
        self.assertNotIn("勝率", html)

    def test_empty_lane_has_explicit_empty_state(self) -> None:
        html = render_v4_lane({"signals": {"selling": []}}, "selling")
        self.assertIn("目前沒有符合此狀態", html)


if __name__ == "__main__":
    unittest.main()
