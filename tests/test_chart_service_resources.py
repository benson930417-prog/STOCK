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
        old_page.set_viewport_size = AsyncMock()
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
        old_page.set_viewport_size.assert_awaited_once_with(
            chart_service.GENERIC_SNAPSHOT_VIEWPORT
        )
        old_page.goto.assert_awaited_once_with(
            chart_service.CHART_TABS["bond"],
            wait_until="networkidle",
            timeout=60000,
        )
        self.assertEqual({"bond": old_page}, chart_service.pages)

    async def test_nasdaq_layout_is_desktop_before_navigation(self) -> None:
        page = Mock()
        page.is_closed.return_value = False
        page.set_viewport_size = AsyncMock()
        page.goto = AsyncMock()
        page.add_style_tag = AsyncMock()
        browser = Mock()
        browser.is_connected.return_value = True
        chart_service.browser_instance = browser
        chart_service.browser_context = Mock()
        chart_service.pages.clear()
        chart_service.pages["oil"] = page

        with patch.object(chart_service.asyncio, "sleep", new=AsyncMock()):
            selected = await chart_service._get_page_for_key("nasdaq")

        self.assertIs(selected, page)
        page.set_viewport_size.assert_awaited_once_with(
            chart_service.NASDAQ_SNAPSHOT_VIEWPORT
        )
        page.goto.assert_awaited_once_with(
            chart_service.CHART_TABS["nasdaq"],
            wait_until="networkidle",
            timeout=60000,
        )
        page.add_style_tag.assert_not_awaited()

    async def test_nasdaq_selects_real_control_before_hiding_layout(self) -> None:
        body = Mock()
        body.evaluate = AsyncMock(return_value="US Tech 100 Cash")
        one_day = Mock()
        one_day.wait_for = AsyncMock()
        one_day.click = AsyncMock()
        one_day.get_attribute = AsyncMock(
            side_effect=["rangeButton-X selected-X", None, None]
        )
        page = Mock()
        page.locator.return_value = body
        page.get_by_role.return_value = one_day
        page.wait_for_load_state = AsyncMock()
        page.add_style_tag = AsyncMock()
        page.evaluate = AsyncMock()

        with patch.object(chart_service.asyncio, "sleep", new=AsyncMock()):
            result = await chart_service._select_ig_nasdaq_one_day(page)

        self.assertTrue(result["selected"])
        page.get_by_role.assert_called_once_with(
            "button", name="1 day", exact=True
        )
        one_day.wait_for.assert_awaited_once_with(
            state="attached", timeout=20000
        )
        one_day.click.assert_awaited_once_with(timeout=5000)
        page.add_style_tag.assert_awaited_once_with(
            content=chart_service.HIDE_CSS
        )

    async def test_nasdaq_crop_tracks_canvas_y_but_keeps_overlay_width(self) -> None:
        page = Mock()
        page.evaluate = AsyncMock(return_value={
            "x": 0,
            "y": 88,
            "width": 1200,
            "height": 362,
        })
        measured = await chart_service._ig_nasdaq_chart_clip(
            page, chart_service.SnapshotRequest(key="nasdaq")
        )
        self.assertEqual(
            measured,
            {"x": 0, "y": 88, "width": 1200, "height": 362},
        )

        tuned = await chart_service._ig_nasdaq_chart_clip(
            page,
            chart_service.SnapshotRequest(
                key="nasdaq", crop_y=90, crop_height=350
            ),
        )
        self.assertEqual(tuned["y"], 90)
        self.assertEqual(tuned["height"], 350)
        self.assertEqual(tuned["width"], 1200)

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
