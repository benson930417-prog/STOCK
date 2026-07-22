from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from scripts.generate_etf_action_insight import render_line_text
from scripts.line_active_report_payload import ACTIVE_TICKERS, build_active_report_messages


class LineActionPayloadTests(unittest.TestCase):
    def test_four_active_reports_are_one_text_plus_four_images(self) -> None:
        tickers = ACTIVE_TICKERS
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            for ticker in tickers:
                (data_dir / f"etf_{ticker}_history.json").write_text(
                    json.dumps({"2026-07-22": {}}), encoding="utf-8"
                )
            cache = data_dir / "etf_action_insight.json"
            cache.write_text(
                json.dumps({"line_text": "🔥 cached ETF action juice"}),
                encoding="utf-8",
            )

            messages = build_active_report_messages(
                tickers,
                data_dir=data_dir,
                action_cache=cache,
                webhook_host="https://example.test",
                cache_buster=123,
            )

        self.assertEqual(5, len(messages))
        self.assertEqual("text", messages[0]["type"])
        self.assertIn("cached ETF action juice", messages[0]["text"])
        self.assertEqual(["image"] * 4, [row["type"] for row in messages[1:]])
        self.assertTrue(all("?t=123" in row["originalContentUrl"] for row in messages[1:]))

    def test_fifth_active_image_is_rejected_instead_of_second_broadcast(self) -> None:
        with self.assertRaisesRegex(ValueError, "at most 4 active ETF images"):
            build_active_report_messages(
                ["A", "B", "C", "D", "E"],
                data_dir=Path("unused"),
                action_cache=Path("unused"),
            )

    def test_action_text_is_stock_level_and_plain(self) -> None:
        event = {
            "name": "台達電",
            "category": "電源供應器",
            "event_label": "賣後轉買",
            "reason": "06/22–07/03 曾明顯減碼後，3 檔 ETF 轉買",
            "confirmation_label": "3/3 ETF 同步",
        }
        text = render_line_text(
            "2026-07-22",
            {"buying": [event], "holding": [], "selling": []},
        )

        self.assertIn("台達電（電源供應器）｜賣後轉買", text)
        self.assertIn("3/3 ETF 同步", text)
        self.assertNotIn("類股洞察", text)


if __name__ == "__main__":
    unittest.main()
