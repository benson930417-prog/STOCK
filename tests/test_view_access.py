import hashlib
import hmac
import unittest
from src.view_access import valid_session


class ViewAccessTests(unittest.TestCase):
    def token(self, stamp, password='test-password'):
        sig = hmac.new(password.encode(), f'stock:{stamp}'.encode(), hashlib.sha256).hexdigest()[:32]
        return f'{stamp}-{sig}'

    def test_existing_signed_links_remain_valid(self):
        self.assertTrue(valid_session(self.token(100000), 'test-password', now=100030))

    def test_expired_future_wrong_password_and_malformed_links_fail(self):
        for token, password, now in [(self.token(100000), 'test-password', 186401),
                                      (self.token(100000), 'test-password', 99939),
                                      (self.token(100000), 'wrong', 100000),
                                      ('invalid', 'test-password', 100000),
                                      (self.token(100000), '', 100000)]:
            with self.subTest(now=now, password=password):
                self.assertFalse(valid_session(token, password, now=now))
