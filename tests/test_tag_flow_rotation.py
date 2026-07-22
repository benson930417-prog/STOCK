from __future__ import annotations

from datetime import date, timedelta
import unittest

from src.tag_flow_rotation import build_rotation_snapshot


ETFS = ["00403A", "00981A", "00991A"]


def _fixture(
    values_by_etf: dict[str, list[float]],
    *,
    category: str = "被動元件",
) -> dict:
    session_count = len(next(iter(values_by_etf.values())))
    dates = [
        (date(2026, 1, 1) + timedelta(days=index)).isoformat()
        for index in range(session_count)
    ]
    observations = []
    for etf in ETFS:
        values = values_by_etf[etf]
        if len(values) != session_count:
            raise ValueError("all ETF fixtures must have the same length")
        for session, value in zip(dates, values):
            observations.append(
                {
                    "etf": etf,
                    "date": session,
                    "stocks": [
                        {
                            "id": "TEST",
                            "name": "測試股票",
                            "category": category,
                            # Concepts must never participate in category logic.
                            "concepts": ["錯誤概念分類", "熱門題材"],
                            "flow": value,
                        }
                    ],
                }
            )
    return {
        "etfs": ETFS,
        "dates": {"by_etf": {etf: dates for etf in ETFS}},
        "observations": observations,
    }


class RotationSnapshotTests(unittest.TestCase):
    def test_chart_window_never_changes_story_or_rank(self) -> None:
        signal = [0.05, 0.1, -0.03, 0.2, 0.15] * 6
        data = _fixture({etf: signal for etf in ETFS})

        snapshots = {
            days: build_rotation_snapshot(data, ETFS, chart_days=days)
            for days in (5, 10, 20)
        }
        rows = {days: snapshot["rows"][0] for days, snapshot in snapshots.items()}

        invariant_fields = (
            "phase",
            "phase_group",
            "candidate_phase",
            "pending_phase",
            "fast",
            "trend",
            "background",
            "strength_percentile",
            "strength_label",
            "buyers",
            "sellers",
            "recent_3_total",
            "recent_3_buyers",
            "recent_3_sellers",
            "recent_sell_alert",
            "confidence",
            "window_totals",
            "cross_section_rank",
        )
        baseline = {field: rows[5][field] for field in invariant_fields}
        for days in (10, 20):
            self.assertEqual(
                baseline,
                {field: rows[days][field] for field in invariant_fields},
            )
        self.assertEqual(5, len(rows[5]["chart_dates"]))
        self.assertEqual(10, len(rows[10]["chart_dates"]))
        self.assertEqual(20, len(rows[20]["chart_dates"]))

    def test_rotation_boundary_is_a_transition_not_a_window_flip(self) -> None:
        # A long accumulation followed by three coordinated sell sessions:
        # recent pressure is negative, but the 20-session background is still
        # positive.  This is a rotation transition, not a contradictory story.
        signal = [0.5] * 16 + [-1.2] * 4
        data = _fixture({etf: signal for etf in ETFS})

        for days in (5, 10, 20):
            row = build_rotation_snapshot(data, ETFS, chart_days=days)["rows"][0]
            self.assertLess(row["fast"], 0)
            self.assertGreater(row["background"], 0)
            self.assertEqual("recent_selling", row["phase"])
            self.assertEqual("sell", row["phase_group"])
            self.assertTrue(row["recent_sell_alert"])
            self.assertEqual(3, row["recent_3_sellers"])

    def test_one_large_etf_cannot_create_a_confirmed_buy_story(self) -> None:
        data = _fixture(
            {
                "00403A": [5.0] * 20,
                "00981A": [0.0] * 20,
                "00991A": [0.0] * 20,
            }
        )
        row = build_rotation_snapshot(data, ETFS)["rows"][0]

        self.assertEqual(1, row["buyers"])
        self.assertNotEqual("buy", row["phase_group"])
        self.assertEqual("低", row["confidence"])

    def test_new_direction_needs_two_consecutive_sessions(self) -> None:
        one_day = _fixture({etf: [0.0] * 14 + [1.0] for etf in ETFS})
        two_days = _fixture({etf: [0.0] * 13 + [1.0, 1.0] for etf in ETFS})

        first = build_rotation_snapshot(one_day, ETFS)["rows"][0]
        second = build_rotation_snapshot(two_days, ETFS)["rows"][0]

        self.assertEqual("no_consensus", first["phase"])
        self.assertIn(first["pending_phase"], {"buy_entering", "buy_accelerating"})
        self.assertEqual("buy", second["phase_group"])
        self.assertIsNone(second["pending_phase"])

    def test_concepts_are_display_only_and_never_create_rows(self) -> None:
        data = _fixture({etf: [0.1] * 20 for etf in ETFS}, category="唯一類股")
        categories = {
            row["category"] for row in build_rotation_snapshot(data, ETFS)["rows"]
        }
        self.assertEqual({"唯一類股"}, categories)


if __name__ == "__main__":
    unittest.main()
