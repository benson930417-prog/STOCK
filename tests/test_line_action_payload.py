from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from scripts.generate_etf_action_insight import (
    PHONE_CONTENT_WIDTH,
    _display_width,
    render_line_text,
)
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
        self.assertTrue(messages[0]["text"].startswith("📊 主動 ETF 操作日報"))
        self.assertIn("403｜升級50\n　　日期：7月22日", messages[0]["text"])
        self.assertIn("cached ETF action juice", messages[0]["text"])
        self.assertNotIn("2026-07-22", messages[0]["text"])
        header = messages[0]["text"].split("\n\n", 1)[0]
        header_lines = [
            line for line in header.splitlines() if line and "━" not in line
        ]
        self.assertLessEqual(
            max(map(_display_width, header_lines)), PHONE_CONTENT_WIDTH
        )
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
            "stock_id": "2308",
            "name": "台達電",
            "category": "電源供應器",
            "event_type": "sell_to_buy",
            "event_label": "賣後轉買",
            "reason": "06/22–07/03 曾明顯減碼後，3 檔 ETF 轉買",
            "confirmation_label": "3/3 ETF 同步",
            "breadth": 3,
            "etfs": ["00403A", "00981A", "00991A"],
        }
        text = render_line_text(
            "2026-07-22",
            {"buying": [event], "holding": [], "selling": []},
        )

        self.assertTrue(text.startswith("主動 ETF 動作\n━━━━━━━━━━━━━━"))
        self.assertIn("截至：7月22日", text)
        self.assertIn("🔴 買進觀察｜1 檔", text)
        self.assertIn("01. 台達電｜2308", text)
        self.assertIn("類股：電源供應器", text)
        self.assertIn("動作：賣後轉買", text)
        self.assertIn("ETF：403・981・991", text)
        self.assertIn("判定：共識（3/3）", text)
        self.assertIn("依據：\n    先前明顯減碼\n    現在轉為買進", text)
        self.assertIn("一般：至少 2/3 同向", text)
        self.assertIn("1/3：只留建倉・出清", text)
        self.assertNotIn("06/22", text)
        self.assertNotIn("類股洞察", text)
        content_lines = [line for line in text.splitlines() if line and "━" not in line]
        self.assertLessEqual(
            max(map(_display_width, content_lines)), PHONE_CONTENT_WIDTH
        )

    def test_long_category_and_evidence_are_split_on_field_boundaries(self) -> None:
        event = {
            "stock_id": "2383",
            "name": "台光電",
            "category": "PCB-材料設備",
            "event_type": "conviction_buy",
            "event_label": "持續加碼",
            "breadth": 3,
            "etfs": ["00403A", "00981A", "00991A"],
            "buy_days": 5,
            "sell_days": 0,
        }
        text = render_line_text(
            "2026-07-22",
            {"buying": [], "holding": [event], "selling": []},
        )

        self.assertIn("  類股：PCB-材料設備", text)
        self.assertIn("  動作：持續加碼", text)
        self.assertIn("  ETF：403・981・991", text)
        self.assertIn("  判定：持續（3檔）", text)
        self.assertIn("  依據：\n    10日5買・0賣\n    最新仍買", text)
        content_lines = [line for line in text.splitlines() if line and "━" not in line]
        self.assertLessEqual(
            max(map(_display_width, content_lines)), PHONE_CONTENT_WIDTH
        )


if __name__ == "__main__":
    unittest.main()
