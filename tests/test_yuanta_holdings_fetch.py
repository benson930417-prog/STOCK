import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_fetcher(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakePage:
    def __init__(self, failures: int):
        self.failures = failures
        self.waits = 0
        self.reloads = 0

    def wait_for_function(self, expression, timeout):
        assert "tbody" in expression
        assert timeout == 6000
        self.waits += 1
        if self.waits <= self.failures:
            raise TimeoutError("component has not hydrated")

    def wait_for_timeout(self, milliseconds):
        assert milliseconds == 1200

    def reload(self, wait_until, timeout):
        assert wait_until == "domcontentloaded"
        assert timeout == 60000
        self.reloads += 1


def test_holdings_table_wait_recovers_after_stalled_hydration():
    fetcher = _load_fetcher("fetch_passive_0050.py")
    page = FakePage(failures=3)

    fetcher._wait_for_holdings_table(page)

    assert page.waits == 4
    assert page.reloads == 1


def test_holdings_table_wait_fails_closed_after_bounded_reloads():
    fetcher = _load_fetcher("fetch_passive_0056.py")
    page = FakePage(failures=10)

    try:
        fetcher._wait_for_holdings_table(page)
    except ValueError as exc:
        assert "Missing hydrated 0056" in str(exc)
    else:
        raise AssertionError("stale issuer page must not be accepted")

    assert page.waits == 6
    assert page.reloads == 2
