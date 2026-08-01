import unittest

import pandas as pd

from scripts.fetch_etf_00981A import _find_stock_header_row, _parse_stock_holdings


class Fetch00981AParserTests(unittest.TestCase):
    def test_does_not_accept_futures_header(self):
        frame = pd.DataFrame([["期貨代號", "期貨名稱", "持股權重", "口數"]])

        self.assertEqual(-1, _find_stock_header_row(frame))

    def test_selects_stock_table_after_futures_table(self):
        rows = [
            ["期貨代號", "期貨名稱", "持股權重", "口數"],
            ["TX", "台指期貨(B)", "5.89%", "1,940"],
            ["股票代號", "股票名稱", "股數", "持股權重"],
        ]
        rows.extend([[str(2300 + i), f"股票{i}", "1,000", "5.0%"] for i in range(10)])
        frame = pd.DataFrame(rows)

        header = _find_stock_header_row(frame)
        holdings = _parse_stock_holdings(frame, header)

        self.assertEqual(2, header)
        self.assertEqual(10, len(holdings))
        self.assertNotIn("TX", {row["id"] for row in holdings})
        self.assertTrue(all(row["shares"] == 1000 for row in holdings))

    def test_rejects_rows_without_numeric_shares_and_weights(self):
        rows = [["股票代號", "股票名稱", "股數", "持股權重"]]
        rows.extend([[str(2300 + i), f"股票{i}", None, None] for i in range(12)])
        frame = pd.DataFrame(rows)

        with self.assertRaisesRegex(RuntimeError, "Only parsed 0"):
            _parse_stock_holdings(frame, 0)


if __name__ == "__main__":
    unittest.main()
