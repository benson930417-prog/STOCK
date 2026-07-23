from __future__ import annotations

from datetime import date, timedelta
import unittest

from src.etf_intent_v3 import (
    build_intent_payload,
    disclosed_units,
    flow_adjusted_moves,
)


ETFS = ["00403A", "00981A", "00991A"]


def _day(
    session: str,
    *,
    units: int | None,
    fund_size: float,
    nav: float,
    shares: dict[str, int],
) -> dict:
    meta = {"fund_size": fund_size, "nav": nav}
    if units is not None:
        meta["outstanding_units"] = units
    return {
        "date": session,
        "meta": meta,
        "holdings": [
            {
                "id": stock_id,
                "name": stock_id,
                "shares": amount,
                "weight_pct": 5.0,
            }
            for stock_id, amount in shares.items()
        ],
    }


class EtfIntentV3Tests(unittest.TestCase):
    def test_exact_units_override_nav_estimate(self) -> None:
        units, quality = disclosed_units(
            _day(
                "2026-01-01",
                units=123_000,
                fund_size=1_250_000,
                nav=10.0,
                shares={},
            )
        )
        self.assertEqual(123_000, units)
        self.assertEqual("exact", quality)

    def test_mechanical_portfolio_scale_has_zero_active_flow(self) -> None:
        previous = _day(
            "2026-01-01",
            units=100,
            fund_size=1_000,
            nav=10.0,
            shares={"2330": 1_000},
        )
        current = _day(
            "2026-01-02",
            units=120,
            fund_size=1_200,
            nav=10.0,
            shares={"2330": 1_200},
        )
        move = flow_adjusted_moves(
            current,
            previous,
            etf="00403A",
            date="2026-01-02",
            tags={},
        )[0]
        self.assertEqual(200, move["raw_delta_shares"])
        self.assertAlmostEqual(0.0, move["active_delta_shares"])
        self.assertAlmostEqual(0.0, move["active_flow"])

    def test_flow_adjusted_direction_that_opposes_actual_trade_is_not_copyable(self) -> None:
        previous = _day(
            "2026-01-01",
            units=100,
            fund_size=1_000,
            nav=10.0,
            shares={"2330": 1_000},
        )
        current = _day(
            "2026-01-02",
            units=120,
            fund_size=1_200,
            nav=10.0,
            shares={"2330": 1_050},
        )
        move = flow_adjusted_moves(
            current,
            previous,
            etf="00403A",
            date="2026-01-02",
            tags={},
        )[0]
        self.assertGreater(move["raw_delta_shares"], 0)
        self.assertLess(move["active_flow"], 0)
        self.assertFalse(move["copyable"])

    def test_v3_exposes_only_two_lanes_and_ignores_concepts(self) -> None:
        sessions = [
            (date(2026, 1, 1) + timedelta(days=index)).isoformat()
            for index in range(24)
        ]
        histories = {}
        for etf_index, etf in enumerate(ETFS):
            history = {}
            shares = 1_000
            for index, session in enumerate(sessions):
                if index == len(sessions) - 1 and etf_index < 2:
                    shares += 200
                history[session] = _day(
                    session,
                    units=100_000,
                    fund_size=1_000_000,
                    nav=10.0,
                    shares={"2330": shares},
                )
            histories[etf] = history
        payload = build_intent_payload(
            histories,
            {
                "2330": {
                    "name": "台積電",
                    "category": "IC-代工",
                    "concepts": [{"name": "不得使用"}],
                }
            },
        )
        self.assertEqual({"buying", "selling"}, set(payload["signals"]))
        self.assertTrue(payload["signals"]["buying"])
        self.assertFalse(payload["signals"]["selling"])
        rendered = str(payload["signals"])
        self.assertNotIn("不得使用", rendered)

    def test_single_etf_never_surfaces_even_for_extreme_new_position(self) -> None:
        sessions = [
            (date(2026, 1, 1) + timedelta(days=index)).isoformat()
            for index in range(24)
        ]
        histories = {}
        for etf_index, etf in enumerate(ETFS):
            history = {}
            for index, session in enumerate(sessions):
                shares = {}
                if etf_index == 0 and index == len(sessions) - 1:
                    shares = {"6805": 100_000}
                history[session] = _day(
                    session,
                    units=100_000,
                    fund_size=1_000_000,
                    nav=10.0,
                    shares=shares,
                )
            histories[etf] = history
        payload = build_intent_payload(histories, {})
        self.assertFalse(payload["signals"]["buying"])
        self.assertFalse(payload["signals"]["selling"])
        self.assertFalse(payload["methodology"]["single_etf_exceptions"])
        self.assertEqual(
            2,
            payload["methodology"]["minimum_same_direction_etfs"],
        )

    def test_previous_consensus_without_current_confirmation_is_hidden(self) -> None:
        sessions = [
            (date(2026, 1, 1) + timedelta(days=index)).isoformat()
            for index in range(24)
        ]
        histories = {}
        for etf_index, etf in enumerate(ETFS):
            history = {}
            shares = 1_000
            for index, session in enumerate(sessions):
                if index == len(sessions) - 2 and etf_index < 2:
                    shares += 200
                history[session] = _day(
                    session,
                    units=100_000,
                    fund_size=1_000_000,
                    nav=10.0,
                    shares={"2330": shares},
                )
            histories[etf] = history
        payload = build_intent_payload(histories, {})
        self.assertFalse(payload["signals"]["buying"])
        self.assertTrue(
            any(
                event["signal_date"] == sessions[-2]
                for event in payload["events"]
            )
        )

    def test_continuing_buy_without_new_acceleration_is_not_a_hold_lane(self) -> None:
        sessions = [
            (date(2026, 2, 1) + timedelta(days=index)).isoformat()
            for index in range(25)
        ]
        histories = {}
        for etf in ETFS:
            history = {}
            shares = 1_000
            for index, session in enumerate(sessions):
                if etf != "00991A" and index >= 19:
                    shares += 100
                history[session] = _day(
                    session,
                    units=100_000,
                    fund_size=1_000_000,
                    nav=10.0,
                    shares={"2383": shares},
                )
            histories[etf] = history
        payload = build_intent_payload(histories, {})
        self.assertEqual({"buying", "selling"}, set(payload["signals"]))
        self.assertNotIn("holding", payload["signals"])
        followthroughs = [
            event
            for event in payload["events"]
            if event["event_type"] == "buy_followthrough"
        ]
        self.assertLessEqual(len(followthroughs), 1)


if __name__ == "__main__":
    unittest.main()
