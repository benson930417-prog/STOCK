import unittest
from scripts.line_active_report_payload import ACTIVE_TICKERS, build_active_report_messages


class LineActiveReportPayloadTests(unittest.TestCase):
    def test_four_reports_keep_one_batch_and_per_fund_holding_dates(self):
        messages = build_active_report_messages(
            ACTIVE_TICKERS, webhook_host='https://example.test', cache_buster=123,
            history_loader=lambda ticker: {'2026-09-11' if ticker == '00988A' else '2026-09-14': {}},
        )
        self.assertEqual(['text'] + ['image'] * 4, [m['type'] for m in messages])
        self.assertIn('9月11日', messages[0]['text'])
        self.assertIn('9月14日', messages[0]['text'])
        self.assertTrue(all('?t=123' in m['originalContentUrl'] for m in messages[1:]))

    def test_too_many_images_or_missing_holdings_fail_before_publication(self):
        with self.assertRaises(ValueError):
            build_active_report_messages(['A', 'B', 'C', 'D', 'E'])
        with self.assertRaises(RuntimeError):
            build_active_report_messages(ACTIVE_TICKERS, history_loader=lambda _: {})
