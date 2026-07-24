from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from scripts.generate_etf_consensus_v4_summary import (
    allocate_pages,
    build_page_specs,
    render_lane_html,
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

    def test_long_lanes_use_all_five_line_images(self) -> None:
        make_cards = lambda count: [  # noqa: E731
            {"stock_id": str(index)} for index in range(count)
        ]
        payload = {
            "signals": {
                "watching": make_cards(12),
                "buying": make_cards(13),
                "selling": make_cards(2),
            }
        }
        self.assertEqual(
            {"watching": 2, "buying": 2, "selling": 1},
            allocate_pages(payload),
        )
        specs = build_page_specs(payload)
        self.assertEqual(5, len(specs))
        self.assertEqual(
            ["buying", "buying", "selling", "watching", "watching"],
            [spec["lane_key"] for spec in specs],
        )
        self.assertEqual(27, sum(len(spec["cards"]) for spec in specs))

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
