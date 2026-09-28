import ast
import asyncio
import hashlib
import json
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from scripts import chart_service as chart
from scripts import monitor_market_charts as monitor
from scripts import overlay_market_sessions as overlay
from scripts.chart_runtime import BrowserRequestGuard


class RenderingContractTests(unittest.TestCase):
    def test_original_sources_extraction_one_day_crop_overlay_and_quotes_unchanged(self):
        root = Path(__file__).resolve().parents[1]
        expected = json.loads((root/'tests/fixtures/chart_rendering_contract.json').read_text())
        nodes = ast.parse((root/'scripts/chart_service.py').read_text(encoding='utf-8')).body
        actual = {}
        for node in nodes:
            key = getattr(node, 'name', None)
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
                key = node.targets[0].id
            if key in expected:
                actual[key] = hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()
        self.assertEqual(expected, actual)

    def test_friday_weekend_and_weekday_axis_contract(self):
        for day, hour, frame in [(24, 12, 24), (25, 12, 22), (26, 12, 22), (27, 12, 22), (28, 5, 22), (28, 12, 24)]:
            with self.subTest(day=day, hour=hour):
                scale, start = overlay.axis_scale(datetime(2026,9,day,hour,tzinfo=overlay.TW))
                self.assertAlmostEqual(scale, (1095-31)/frame)
        self.assertEqual(overlay.tw_offset_hours(datetime(2026,9,25).date()), 12)
        self.assertEqual(overlay.tw_offset_hours(datetime(2026,12,25).date()), 13)


class RequestGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_finishes_handler_cancellation_before_unlock(self):
        ended = asyncio.Event()
        async def handler(*args):
            try: await asyncio.sleep(60)
            finally: ended.set()
        lock = asyncio.Lock(); failed = Mock(); messages = []
        guard = BrowserRequestGuard(handler, lock=lock, on_failure=failed, work_seconds=.01)
        async def send(message): messages.append(message)
        await guard({'type':'http','path':'/snapshot'}, AsyncMock(), send)
        self.assertTrue(ended.is_set())
        self.assertFalse(lock.locked())
        failed.assert_called_once()
        self.assertEqual(messages[0]['status'],503)

    async def test_full_queue_does_not_start_or_invalidate_active_capture(self):
        lock=asyncio.Lock(); await lock.acquire()
        handler=AsyncMock(); failed=Mock(); messages=[]
        guard=BrowserRequestGuard(handler,lock=lock,on_failure=failed,queue_seconds=.01)
        async def send(message):messages.append(message)
        await guard({'type':'http','path':'/snapshot'},AsyncMock(),send)
        self.assertTrue(lock.locked());handler.assert_not_awaited();failed.assert_not_called()
        self.assertEqual(messages[0]['status'],503)
        lock.release()

    async def test_browser_crash_is_recoverable_and_health_bypasses_queue(self):
        failed=Mock()
        guard=BrowserRequestGuard(AsyncMock(side_effect=RuntimeError('browser crash')),
                                 lock=asyncio.Lock(),on_failure=failed)
        messages=[]
        async def send(message):messages.append(message)
        await guard({'type':'http','path':'/snapshot'},AsyncMock(),send)
        self.assertEqual(messages[0]['status'],503);failed.assert_called_once()
        guard.app=AsyncMock();await guard.lock.acquire()
        await guard({'type':'http','path':'/healthz'},AsyncMock(),send)
        guard.app.assert_awaited_once();guard.lock.release()


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        for name in ['SCHEDULE_STATE_PATH','OUTAGE_STATE_PATH']:
            p=patch.object(monitor,name,Path(self.tmp.name)/name);p.start();self.addCleanup(p.stop)

    def test_one_failed_symbol_cools_down_without_starving_others(self):
        keys=['oil','nasdaq','gold'];state=monitor.load_schedule(keys,1000)
        def refresh(key,timeout):
            if key=='nasdaq':raise monitor.ChartServiceUnavailable('blocked',300)
        with patch.object(monitor,'refresh_key',side_effect=refresh) as fetch:
            monitor.run_due_refreshes(keys,state,clock=lambda:1000)
        self.assertEqual(fetch.call_count,3)
        self.assertEqual(state['keys']['nasdaq']['next_due'],1300)
        self.assertEqual(state['keys']['gold']['next_due'],1060)
        self.assertEqual(monitor.load_schedule(keys,1001)['keys'],state['keys'])

    def test_two_blocked_symbols_stop_provider_and_restart_preserves_cooldown(self):
        keys=['oil','gold','nasdaq'];state=monitor.load_schedule(keys,1000)
        with patch.object(monitor,'refresh_key',side_effect=monitor.ChartServiceUnavailable('blocked',300)) as fetch:
            monitor.run_due_refreshes(keys,state,clock=lambda:1000)
            self.assertEqual(fetch.call_count,2)
            state=monitor.load_schedule(keys,1001)
            monitor.run_due_refreshes(keys,state,clock=lambda:1001)
            self.assertEqual(fetch.call_count,2)
        self.assertEqual(state['provider_until'],1300)

    def test_no_extra_minute_sleep_after_a_slow_refresh_and_no_catchup_burst(self):
        state=monitor.load_schedule(['oil'],1000);clock_values=iter([1000,1000,1090,1090])
        with patch.object(monitor,'refresh_key'):
            monitor.run_due_refreshes(['oil'],state,clock=lambda:next(clock_values))
        self.assertEqual(state['keys']['oil']['next_due'],1091)
        self.assertEqual(monitor.next_schedule_delay(['oil'],state,now_epoch=1090),1)

    def test_old_provider_cooldown_is_not_bypassed_by_deployment(self):
        monitor.record_outage(2,now_epoch=1000)
        state=monitor.load_schedule(['nasdaq'],1001)
        self.assertEqual(state['provider_until'],1600)
