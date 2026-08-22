from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest
from datetime import datetime
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from scripts import monitor_market_charts
from src.market_chart_cache import (
    effective_market_cache_max_age,
    MarketImageChecksumMismatch,
    NASDAQ_CLOSED_CACHE_MAX_AGE_SECONDS,
    file_sha256,
    freeze_market_reply_image,
)


class MarketChartCacheTests(unittest.TestCase):
    def _cache(self, data: bytes) -> dict:
        return {
            "key": "nasdaq",
            "updated_at": "2026-07-24T08:51:09.123456Z",
            "snapshot_url": "nasdaq_chart.png",
            "snapshot_sha256": hashlib.sha256(data).hexdigest(),
        }

    def test_frozen_reply_survives_mutable_source_replacement(self) -> None:
        original = b"current intraday chart"
        with tempfile.TemporaryDirectory() as tmp:
            images = Path(tmp)
            source = images / "nasdaq_chart.png"
            source.write_bytes(original)
            frozen_name = freeze_market_reply_image(self._cache(original), images)
            source.write_bytes(b"next minute chart")

            frozen = images / frozen_name
            self.assertEqual(original, frozen.read_bytes())
            self.assertIn(
                "line_market_nasdaq_20260724085109123456_",
                frozen_name,
            )
            self.assertEqual(
                hashlib.sha256(original).hexdigest(),
                file_sha256(frozen),
            )

    def test_checksum_mismatch_rejects_text_image_race(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            images = Path(tmp)
            (images / "nasdaq_chart.png").write_bytes(b"newer chart")
            with self.assertRaises(MarketImageChecksumMismatch):
                freeze_market_reply_image(self._cache(b"older chart"), images)

    def test_checksum_is_required_for_line_reply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            images = Path(tmp)
            (images / "nasdaq_chart.png").write_bytes(b"chart")
            cache = self._cache(b"chart")
            cache.pop("snapshot_sha256")
            with self.assertRaises(MarketImageChecksumMismatch):
                freeze_market_reply_image(cache, images)

    def test_nasdaq_weekend_cache_matches_the_real_closed_market(self) -> None:
        london = ZoneInfo("Europe/London")
        saturday = datetime(2026, 8, 22, 20, 55, tzinfo=london)
        sunday_open = datetime(2026, 8, 23, 23, 1, tzinfo=london)
        self.assertEqual(
            NASDAQ_CLOSED_CACHE_MAX_AGE_SECONDS,
            effective_market_cache_max_age("nasdaq", 240, now=saturday),
        )
        self.assertEqual(
            240,
            effective_market_cache_max_age("nasdaq", 240, now=sunday_open),
        )
        self.assertEqual(
            240,
            effective_market_cache_max_age("oil", 240, now=saturday),
        )

    def test_monitor_commits_service_verified_checksum(self) -> None:
        data = b"same-moment chart"
        digest = hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "nasdaq_chart.png"
            image.write_bytes(data)
            response = {
                "url": image.name,
                "path": str(image),
                "text": "NASDAQ quote",
                "quote": {"price": 123},
                "sha256": digest,
                "size": len(data),
            }
            with (
                patch.object(
                    monitor_market_charts,
                    "QUOTE_CACHE_DIR",
                    root / "cache",
                ),
                patch.object(
                    monitor_market_charts,
                    "post_chart_service",
                    return_value=response,
                ),
            ):
                payload = monitor_market_charts.refresh_key("nasdaq", 10)
            self.assertEqual(digest, payload["snapshot_sha256"])
            self.assertEqual(len(data), payload["snapshot_size"])

    def test_monitor_rejects_image_changed_after_service_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "nasdaq_chart.png"
            image.write_bytes(b"new bytes")
            response = {
                "url": image.name,
                "path": str(image),
                "text": "NASDAQ quote",
                "sha256": hashlib.sha256(b"old bytes").hexdigest(),
            }
            with patch.object(
                monitor_market_charts,
                "post_chart_service",
                return_value=response,
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "changed before cache commit",
                ):
                    monitor_market_charts.refresh_key("nasdaq", 10)

    def test_upstream_block_stops_the_cycle_and_uses_long_backoff(self) -> None:
        blocked = monitor_market_charts.ChartServiceUnavailable(
            "TradingView upstream blocked",
            retry_after_seconds=monitor_market_charts.UPSTREAM_BLOCKED_BACKOFF_SECONDS,
        )
        with patch.object(
            monitor_market_charts,
            "refresh_key",
            side_effect=blocked,
        ) as refresh:
            backoff = monitor_market_charts.refresh_cycle(
                ["oil", "brent", "bond"],
                10,
            )

        self.assertEqual(
            monitor_market_charts.UPSTREAM_BLOCKED_BACKOFF_SECONDS,
            backoff,
        )
        refresh.assert_called_once_with("oil", 10)

    def test_503_block_detail_is_classified_for_backoff(self) -> None:
        response = Mock()
        response.status_code = 503
        response.json.return_value = {
            "detail": "TradingView upstream blocked: 403 ERROR"
        }
        with patch.object(
            monitor_market_charts.requests,
            "post",
            return_value=response,
        ):
            with self.assertRaises(
                monitor_market_charts.ChartServiceUnavailable
            ) as raised:
                monitor_market_charts.post_chart_service("snapshot", "oil", 10)

        self.assertEqual(
            monitor_market_charts.UPSTREAM_BLOCKED_BACKOFF_SECONDS,
            raised.exception.retry_after_seconds,
        )

    def test_shared_outage_backoff_is_exponential_and_bounded(self) -> None:
        self.assertEqual(300, monitor_market_charts.upstream_blocked_backoff_seconds(1))
        self.assertEqual(600, monitor_market_charts.upstream_blocked_backoff_seconds(2))
        self.assertEqual(1200, monitor_market_charts.upstream_blocked_backoff_seconds(3))
        self.assertEqual(1800, monitor_market_charts.upstream_blocked_backoff_seconds(4))
        self.assertEqual(1800, monitor_market_charts.upstream_blocked_backoff_seconds(20))

    def test_shared_outage_cooldown_survives_process_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "outage.json"
            with patch.object(monitor_market_charts, "OUTAGE_STATE_PATH", state_path):
                delay = monitor_market_charts.record_outage(3, now_epoch=1000)
                state = monitor_market_charts.load_outage_state(now_epoch=1001)
                self.assertEqual(1200, delay)
                self.assertEqual(3, state["consecutive_outages"])
                self.assertEqual(2200, state["next_retry_epoch"])
                stale = monitor_market_charts.load_outage_state(
                    now_epoch=1000 + monitor_market_charts.UPSTREAM_BLOCKED_STATE_MAX_AGE_SECONDS + 1
                )
                self.assertEqual(0, stale["consecutive_outages"])
                monitor_market_charts.clear_outage_state()
                self.assertFalse(state_path.exists())

    def test_shared_block_does_not_starve_independent_nasdaq(self) -> None:
        with (
            patch.object(
                monitor_market_charts,
                "refresh_cycle",
                side_effect=[
                    monitor_market_charts.UPSTREAM_BLOCKED_BACKOFF_SECONDS,
                    0,
                ],
            ) as refresh,
            patch.object(
                monitor_market_charts,
                "record_outage",
                return_value=600,
            ) as record,
        ):
            state = monitor_market_charts.refresh_iteration(
                ["oil", "brent", "nasdaq"],
                10,
                consecutive_outages=0,
                next_retry_epoch=0,
                now_epoch=1000,
            )

        self.assertEqual(
            [call.args[0] for call in refresh.call_args_list],
            [["oil", "brent"], ["nasdaq"]],
        )
        record.assert_called_once_with(1, now_epoch=1000.0)
        self.assertEqual(
            state,
            {"consecutive_outages": 1, "next_retry_epoch": 1600.0},
        )

    def test_persisted_shared_cooldown_refreshes_only_nasdaq(self) -> None:
        with patch.object(
            monitor_market_charts,
            "refresh_cycle",
            return_value=0,
        ) as refresh:
            state = monitor_market_charts.refresh_iteration(
                ["oil", "brent", "nasdaq"],
                10,
                consecutive_outages=3,
                next_retry_epoch=2200,
                now_epoch=1001,
            )

        refresh.assert_called_once_with(["nasdaq"], 10)
        self.assertEqual(
            state,
            {"consecutive_outages": 3, "next_retry_epoch": 2200},
        )


if __name__ == "__main__":
    unittest.main()
