from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout

from scripts import fetch_etf_00991A as fetcher


class FuhHwaFailureTests(unittest.TestCase):
    def test_cached_history_plus_http_200_html_is_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history = root/"history.json"
            today = datetime.now().date().isoformat()
            original = json.dumps({today: {"holdings": [{"id": "2330"}]}})
            history.write_text(original, encoding="utf-8")
            log = root/"log.json"
            with patch.object(fetcher, "DATA_DIR", str(root)), patch.object(fetcher, "HISTORY_FILE", str(history)), patch.object(fetcher, "LOG_FILE", str(log)), patch.object(fetcher.requests, "get", return_value=SimpleNamespace(status_code=200, content=b"<html>not a disclosure</html>")) as request, redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError, "no valid issuer disclosure"):
                    fetcher.fetch_and_update_00991A()
            self.assertEqual(original, history.read_text(encoding="utf-8"))
            self.assertTrue(json.loads(log.read_text())["status"].startswith("FAILED:"))
            self.assertTrue(any(today.replace("-", "") in call.args[0] for call in request.call_args_list))


if __name__ == "__main__":
    unittest.main()
