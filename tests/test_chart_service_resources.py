from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException

from scripts import chart_service


class ChartServiceResourceTests(unittest.TestCase):
    def test_font_download_is_lazy_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            font_dir = Path(tmp) / "fonts"
            font_path = font_dir / "font.otf"

            def write_font(_url, target):
                Path(target).write_bytes(b"font")

            with (
                patch.object(chart_service, "FONT_DIR", str(font_dir)),
                patch.object(chart_service, "FONT_PATH", str(font_path)),
                patch.object(
                    chart_service.urllib.request,
                    "urlretrieve",
                    side_effect=write_font,
                ) as download,
            ):
                chart_service._ensure_cjk_font()
                chart_service._ensure_cjk_font()

            self.assertEqual(b"font", font_path.read_bytes())
            download.assert_called_once()


class ChartPageLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.original_playwright = chart_service.playwright_instance
        self.original_context = chart_service.browser_context
        self.original_browser = chart_service.browser_instance
        self.original_pages = chart_service.pages.copy()

    def tearDown(self) -> None:
        chart_service.playwright_instance = self.original_playwright
        chart_service.browser_context = self.original_context
        chart_service.browser_instance = self.original_browser
        chart_service.pages.clear()
        chart_service.pages.update(self.original_pages)

    async def test_switching_keys_reuses_the_resident_page(self) -> None:
        old_page = Mock()
        old_page.is_closed.return_value = False
        old_page.close = AsyncMock()
        old_page.goto = AsyncMock()
        old_page.add_style_tag = AsyncMock()
        context = Mock()
        context.new_page = AsyncMock()
        browser = Mock()
        browser.is_connected.return_value = True
        chart_service.browser_context = context
        chart_service.browser_instance = browser
        chart_service.pages.clear()
        chart_service.pages["oil"] = old_page

        with patch.object(chart_service.asyncio, "sleep", new=AsyncMock()):
            selected = await chart_service._get_page_for_key("bond")

        self.assertIs(selected, old_page)
        old_page.close.assert_not_awaited()
        context.new_page.assert_not_awaited()
        old_page.goto.assert_awaited_once_with(
            chart_service.CHART_TABS["bond"],
            wait_until="networkidle",
            timeout=60000,
        )
        self.assertEqual({"bond": old_page}, chart_service.pages)

    async def test_unknown_key_fails_before_browser_work(self) -> None:
        chart_service.browser_context = None
        chart_service.browser_instance = None
        chart_service.pages.clear()
        with self.assertRaises(HTTPException) as raised:
            await chart_service._get_page_for_key("not-a-chart")
        self.assertEqual(404, raised.exception.status_code)

    async def test_browser_startup_does_not_preload_chart_pages(self) -> None:
        context = Mock()
        context.new_page = AsyncMock()
        browser = Mock()
        browser.new_context = AsyncMock(return_value=context)
        browser.close = AsyncMock()
        playwright = Mock()
        playwright.chromium.launch = AsyncMock(return_value=browser)
        playwright.stop = AsyncMock()
        starter = Mock()
        starter.start = AsyncMock(return_value=playwright)
        chart_service.playwright_instance = None
        chart_service.browser_context = None
        chart_service.browser_instance = None
        chart_service.pages.clear()

        with patch.object(chart_service, "async_playwright", return_value=starter):
            await chart_service.init_browser()

        context.new_page.assert_not_awaited()
        self.assertEqual({}, chart_service.pages)


class ChartServiceUnitTests(unittest.TestCase):
    def test_chart_service_has_a_measured_single_cpu_resource_envelope(self) -> None:
        root = Path(__file__).resolve().parents[1]
        unit = (root / "services" / "stock-chart.service").read_text(encoding="utf-8")
        self.assertIn("Slice=stock-background.slice", unit)
        self.assertIn("MemoryMax=768M", unit)
        self.assertIn("TasksMax=200", unit)
        self.assertNotIn("MemoryMax=2500M", unit)


if __name__ == "__main__":
    unittest.main()
