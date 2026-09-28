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
        self.original_navigations = chart_service.browser_navigations
        self.original_started = chart_service.browser_started_at
        self.memory_patch = patch.object(chart_service, 'service_memory_bytes', return_value=100)
        self.memory_patch.start()
        self.original_loaded = chart_service.page_loaded_at.copy()
        self.original_used = chart_service.page_last_used.copy()
        self.original_invalid = chart_service.invalid_pages.copy()
        chart_service.page_loaded_at.clear()
        chart_service.page_last_used.clear()
        chart_service.invalid_pages.clear()
        chart_service.browser_navigations = 0
        chart_service.browser_started_at = 0.0

    def tearDown(self) -> None:
        self.memory_patch.stop()
        chart_service.page_loaded_at.clear()
        chart_service.page_loaded_at.update(self.original_loaded)
        chart_service.page_last_used.clear()
        chart_service.page_last_used.update(self.original_used)
        chart_service.invalid_pages.clear()
        chart_service.invalid_pages.update(self.original_invalid)
        chart_service.playwright_instance = self.original_playwright
        chart_service.browser_context = self.original_context
        chart_service.browser_instance = self.original_browser
        chart_service.pages.clear()
        chart_service.pages.update(self.original_pages)
        chart_service.browser_navigations = self.original_navigations
        chart_service.browser_started_at = self.original_started

    async def test_memory_pressure_recycles_before_returning_cached_page(self):
        stale = Mock()
        stale.is_closed.return_value = False
        fresh = Mock()
        fresh.is_closed.return_value = False
        chart_service.browser_instance = Mock()
        chart_service.browser_context = Mock()
        chart_service.pages.clear()
        chart_service.pages['oil'] = stale
        chart_service.service_memory_bytes.return_value = chart_service.BROWSER_SOFT_MEMORY_BYTES

        async def recycle():
            chart_service.pages.clear()
            chart_service.pages['oil'] = fresh
            chart_service.browser_navigations = 0

        with patch.object(chart_service, 'init_browser', new=AsyncMock(side_effect=recycle)) as init:
            self.assertIs(await chart_service._get_page_for_key('oil'), fresh)
        init.assert_awaited_once()

    async def test_healthy_browser_survives_age_and_navigation_count(self):
        chart_service.browser_instance = Mock()
        chart_service.browser_context = Mock()
        chart_service.browser_started_at = 1.0
        chart_service.browser_navigations = 10000
        page = Mock()
        page.is_closed.return_value = False
        chart_service.pages.clear()
        chart_service.pages['oil'] = page
        with patch.object(chart_service.time, 'monotonic', return_value=86400.0), patch.object(
            chart_service, 'init_browser', new=AsyncMock()
        ) as init:
            await chart_service._get_page_for_key('oil')
        init.assert_not_awaited()

    async def test_switching_keys_keeps_both_pages_resident(self) -> None:
        old_page = Mock()
        old_page.is_closed.return_value = False
        old_page.close = AsyncMock()
        old_page.set_viewport_size = AsyncMock()
        old_page.goto = AsyncMock()
        old_page.add_style_tag = AsyncMock()
        context = Mock()
        fresh_page = Mock()
        fresh_page.set_viewport_size = AsyncMock()
        fresh_page.goto = AsyncMock()
        fresh_page.add_style_tag = AsyncMock()
        context.new_page = AsyncMock(return_value=fresh_page)
        browser = Mock()
        browser.is_connected.return_value = True
        chart_service.browser_context = context
        chart_service.browser_instance = browser
        chart_service.pages.clear()
        chart_service.pages["oil"] = old_page

        with patch.object(chart_service.asyncio, "sleep", new=AsyncMock()):
            selected = await chart_service._get_page_for_key("bond")

        self.assertIs(selected, fresh_page)
        old_page.close.assert_not_awaited()
        context.new_page.assert_awaited_once()
        fresh_page.set_viewport_size.assert_awaited_once_with(
            chart_service.GENERIC_SNAPSHOT_VIEWPORT
        )
        fresh_page.goto.assert_awaited_once_with(
            chart_service.CHART_TABS["bond"],
            wait_until="networkidle",
            timeout=60000,
        )
        self.assertEqual({"oil": old_page, "bond": fresh_page}, chart_service.pages)

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
        chart_service.browser_context.new_page = AsyncMock(return_value=page)
        chart_service.pages.clear()

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

    async def test_retained_nasdaq_restores_controls_without_reloading(self):
        page = Mock()
        page.is_closed.return_value = False
        page.evaluate = AsyncMock()
        page.goto = AsyncMock()
        chart_service.pages.clear()
        chart_service.pages['nasdaq'] = page
        chart_service.browser_instance = Mock()
        chart_service.browser_context = Mock()
        result = await chart_service._get_page_for_key('nasdaq')
        self.assertIs(result, page)
        page.goto.assert_not_awaited()
        self.assertEqual(page.evaluate.await_args.args[1], chart_service.HIDE_CSS)

    async def test_invalid_or_aged_page_refreshes_without_restarting_browser(self):
        page=Mock()
        page.is_closed.return_value=False
        page.set_viewport_size=AsyncMock()
        page.goto=AsyncMock()
        page.add_style_tag=AsyncMock()
        chart_service.pages.clear();chart_service.pages['oil']=page
        chart_service.page_loaded_at['oil']=1
        chart_service.browser_instance=Mock();chart_service.browser_context=Mock()
        with patch.object(chart_service,'init_browser',new=AsyncMock()) as init, \
             patch.object(chart_service.time,'monotonic',return_value=602), \
             patch.object(chart_service.asyncio,'sleep',new=AsyncMock()):
            await chart_service._get_page_for_key('oil')
            chart_service.invalid_pages.add('oil')
            await chart_service._get_page_for_key('oil')
        self.assertEqual(page.goto.await_count,2)
        init.assert_not_awaited()
        self.assertNotIn('oil',chart_service.invalid_pages)

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
        self.assertIn("MemoryMax=2560M", unit)
        self.assertIn("KillMode=mixed", unit)
        self.assertIn("RuntimeMaxSec=infinity", unit)
        self.assertIn("TasksMax=512", unit)
        self.assertNotIn("MemoryMax=2500M", unit)


if __name__ == "__main__":
    unittest.main()
