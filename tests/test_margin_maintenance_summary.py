from __future__ import annotations

import unittest

import pandas as pd

from api.webhook import is_margin_risk_command, master_insight_quick_reply
from scripts.generate_margin_maintenance_summary import (
    LINE_IMAGE_WIDTH,
    LINE_SAFE_TOP_HEIGHT,
    render_html,
)


class MarginMaintenanceSummaryTests(unittest.TestCase):
    @staticmethod
    def _cache() -> pd.DataFrame:
        dates = pd.date_range("2026-01-02", periods=130, freq="B")
        return pd.DataFrame(
            {
                "date": dates,
                "estimate_pct": [170 + index * 0.02 for index in range(130)],
                "financing_balance_billion": [7000 + index * 4 for index in range(130)],
                "collateral_value_billion": [12000 + index * 8 for index in range(130)],
                "taiex_close": [22000 + index * 15 for index in range(130)],
                "twse_estimate_pct": [172.0] * 130,
                "tpex_estimate_pct": [168.0] * 130,
                "excluded_etf_collateral_billion": [500.0] * 130,
            }
        )

    def test_mobile_card_contains_web_balance_and_risk_sections(self) -> None:
        rendered = render_html(self._cache())
        self.assertIn(f"width:{LINE_IMAGE_WIDTH}px", rendered)
        self.assertIn(f"height:{LINE_SAFE_TOP_HEIGHT}px", rendered)
        self.assertIn("全市場融資擔保估算率", rendered)
        self.assertIn("近 3 個月融資餘額", rendered)
        self.assertIn("估算率下滑且融資餘額上升", rendered)
        self.assertIn("ETF 只從分子排除", rendered)

    def test_old_cache_without_excluded_etf_audit_column_still_renders(self) -> None:
        cache = self._cache().drop(columns=["excluded_etf_collateral_billion"])
        rendered = render_html(cache)
        self.assertIn("本日排除金額待新快取補齊", rendered)

    def test_line_command_and_master_quick_reply_use_financing_balance_name(self) -> None:
        self.assertTrue(is_margin_risk_command("融資餘額"))
        self.assertTrue(is_margin_risk_command("融資風險"))
        quick_reply = master_insight_quick_reply()
        labels = [item.action.label for item in quick_reply.items]
        texts = [item.action.text for item in quick_reply.items]
        self.assertIn("⚠️ 融資餘額", labels)
        self.assertIn("融資餘額", texts)


if __name__ == "__main__":
    unittest.main()
