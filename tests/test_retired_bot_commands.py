import unittest
from types import SimpleNamespace
from unittest.mock import patch
from api import webhook


class RetiredBotCommandsTests(unittest.TestCase):
    def event(self, text):
        return SimpleNamespace(message=SimpleNamespace(text=text), reply_token='unit-test-only')

    def test_retired_commands_use_no_old_product_and_help_does_not_advertise_them(self):
        for command in ['吳大師', 'ETF共識', 'ETF動作', 'ETF意圖', '市場脈動', '融資餘額']:
            with self.subTest(command=command), patch.object(webhook, 'reply_line') as reply:
                webhook.handle_message(self.event(command))
                for call in reply.call_args_list:
                    messages = call.args[1]
                    if not isinstance(messages, list):
                        messages = [messages]
                    for message in messages:
                        text = getattr(message, 'text', '')
                        self.assertNotIn('吳大師', text)
                        self.assertNotIn('ETF共識', text)
                        self.assertNotIn('ETF動作', text)

    def test_gold_and_basic_etf_identifiers_remain(self):
        with patch.object(webhook, 'reply_cached_market') as reply:
            webhook.handle_message(self.event('黃金'))
            reply.assert_called_once_with('unit-test-only', ['gold'])
        self.assertEqual(webhook.parse_etf_quote_command('981'), '00981A')
        self.assertEqual(webhook.parse_etf_quote_command('0050'), '0050')
