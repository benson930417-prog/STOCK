from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from scripts.generate_etf_consensus_v4_summary import (
    build_page_specs,
    render_lane_html,
    render_wide_html,
    write_html_preview,
)


class EtfConsensusV4SummaryTests(unittest.TestCase):
    def test_each_image_contains_one_complete_lane(self) -> None:
        card = {
            "stock_id": "2308",
            "name": "台達電",
            "category": "電源供應器",
            "state": "buy",
            "direction": 1,
            "score": 70,
            "score_label": "共識強度",
            "score_components": {},
            "transition": "新形成買方共識",
            "participants": ["00403A", "00981A"],
            "etf_label": "403・981",
            "confirmed_date": "2026-07-23",
            "state_days": 1,
            "evidence": [],
            "etf_trends": {},
        }
        payload = {
            "signals": {
                "watching": [],
                "buying": [card],
                "selling": [],
            }
        }
        buy = render_lane_html(payload, "buying")
        watch = render_lane_html(payload, "watching")
        self.assertEqual(1, buy.count('class="tfv4-card tfv4-buy"'))
        self.assertNotIn('class="tfv4-card"', watch)
        self.assertIn("買方共識", buy)
        self.assertIn("單一 ETF 觀察", watch)

    def test_three_wide_images_cover_every_card_once(self) -> None:
        def card(stock_id, tier="", freshness=0):
            return {
                "stock_id": stock_id,
                "decision_tier": tier,
                "score_components": {"freshness": freshness},
            }

        payload = {
            "signals": {
                "watching": [
                    card("w-fresh", freshness=12),
                    card("w-cooling", freshness=5),
                ],
                "buying": [
                    card("b-core", tier="core"),
                    card("b-tracking", tier="tracking"),
                ],
                "selling": [
                    card("s-core", tier="core"),
                    card("s-tracking", tier="tracking"),
                ],
            }
        }
        specs = build_page_specs(payload)
        self.assertEqual(3, len(specs))
        self.assertEqual(
            ["buying", "selling", "watching"],
            [spec["lane_key"] for spec in specs],
        )
        stock_ids = [
            card["stock_id"] for spec in specs for card in spec["cards"]
        ]
        self.assertEqual(6, len(stock_ids))
        self.assertEqual(6, len(set(stock_ids)))
        self.assertEqual(
            [
                "etf_consensus_v4_buy_wide_latest.jpg",
                "etf_consensus_v4_sell_wide_latest.jpg",
                "etf_consensus_v4_watch_wide_latest.jpg",
            ],
            [spec["filename"] for spec in specs],
        )
        html = render_wide_html(payload, specs[0])
        self.assertIn("width:1800px", html)
        self.assertEqual(2, html.count('class="tfv4-lane tfv4-lane-buy"'))
        self.assertIn("買方共識｜順序 1–1", html)
        self.assertIn("買方共識｜順序 2–2", html)

    def test_preview_contains_three_lanes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache.json"
            preview = root / "preview.html"
            cache.write_text(
                json.dumps(
                    {
                        "as_of": "2026-07-23",
                        "signals": {
                            "watching": [],
                            "buying": [],
                            "selling": [],
                        },
                    }
                ),
                encoding="utf-8",
            )
            write_html_preview(preview, cache)
            html = preview.read_text(encoding="utf-8")
        self.assertEqual(3, html.count('class="tfv4-lane tfv4-lane-'))
        self.assertIn("主動 ETF 共識追蹤 V4", html)


if __name__ == "__main__":
    unittest.main()
