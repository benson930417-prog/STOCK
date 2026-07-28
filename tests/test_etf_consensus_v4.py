from __future__ import annotations

from datetime import date, timedelta
import unittest

from src.etf_consensus_v4 import (
    _decision_priority,
    _score,
    assign_v4_action_scales,
    build_consensus_payload,
    hydrate_board,
)


ETFS = ["00403A", "00981A", "00991A"]


def _day(
    session: str,
    shares: dict[str, int],
    *,
    weight_pct: float = 5.0,
) -> dict:
    return {
        "date": session,
        "meta": {
            "fund_size": 1_000_000,
            "nav": 10.0,
            "outstanding_units": 100_000,
        },
        "holdings": [
            {
                "id": stock_id,
                "name": stock_id,
                "shares": amount,
                "weight_pct": weight_pct,
            }
            for stock_id, amount in shares.items()
        ],
    }


def _histories(
    changes: dict[str, dict[int, int]],
    *,
    stock_id: str = "2330",
    sessions: int = 30,
) -> tuple[dict, list[str]]:
    dates = [
        (date(2026, 1, 1) + timedelta(days=index)).isoformat()
        for index in range(sessions)
    ]
    result = {}
    for etf in ETFS:
        shares = 1_000
        history = {}
        for index, session in enumerate(dates):
            shares += int((changes.get(etf) or {}).get(index, 0))
            history[session] = _day(session, {stock_id: shares})
        result[etf] = history
    return result, dates


class EtfConsensusV4Tests(unittest.TestCase):
    def test_core_decision_requires_fresh_meaningful_confirmation(self) -> None:
        core = _decision_priority(
            state="buy",
            score=72,
            components={
                "freshness": 13,
                "relative_strength": 10,
                "joint_persistence": 5,
                "horizon_alignment": 4,
            },
            participants=["00403A", "00981A"],
            state_days=2,
        )
        tracking = _decision_priority(
            state="buy",
            score=72,
            components={
                "freshness": 7,
                "relative_strength": 10,
                "joint_persistence": 18,
                "horizon_alignment": 4,
            },
            participants=["00403A", "00981A"],
            state_days=12,
        )
        self.assertEqual("core", core[0])
        self.assertEqual("tracking", tracking[0])

    def test_ordinary_single_etf_action_never_becomes_consensus(self) -> None:
        histories, _ = _histories({"00403A": {29: 250}})
        payload = build_consensus_payload(histories, {})
        self.assertFalse(payload["signals"]["buying"])
        self.assertFalse(payload["signals"]["selling"])
        self.assertTrue(
            payload["methodology"]["ordinary_single_etf_actions_hidden"]
        )
        self.assertFalse(
            payload["methodology"]["single_etf_score_can_confirm"]
        )

    def test_single_new_position_is_yellow_not_red(self) -> None:
        histories, dates = _histories({}, stock_id="6805")
        for index, session in enumerate(dates):
            histories["00403A"][session] = _day(
                session, {"6805": 500} if index == len(dates) - 1 else {}
            )
            histories["00981A"][session] = _day(session, {})
            histories["00991A"][session] = _day(session, {})
        payload = build_consensus_payload(histories, {})
        self.assertEqual(1, len(payload["signals"]["watching"]))
        self.assertFalse(payload["signals"]["buying"])
        self.assertEqual(
            "new_position", payload["signals"]["watching"][0]["watch_kind"]
        )

    def test_tiny_structural_cleanup_does_not_fill_yellow_lane(self) -> None:
        histories, dates = _histories({}, stock_id="3042")
        for index, session in enumerate(dates):
            shares = {"3042": 1} if index < len(dates) - 1 else {}
            histories["00991A"][session] = _day(
                session, shares, weight_pct=0.001
            )
        payload = build_consensus_payload(histories, {})
        self.assertFalse(payload["signals"]["watching"])

    def test_staggered_two_etf_actions_upgrade_to_red(self) -> None:
        histories, dates = _histories(
            {
                "00403A": {27: 250},
                "00981A": {29: 250},
            }
        )
        payload = build_consensus_payload(histories, {})
        self.assertEqual(1, len(payload["signals"]["buying"]))
        card = payload["signals"]["buying"][0]
        self.assertEqual(2, card["breadth"])
        self.assertEqual(
            {"00403A", "00981A"}, set(card["participants"])
        )
        self.assertEqual(dates[-1], card["confirmed_date"])
        self.assertEqual(3, len(card["etf_trends"]))

    def test_staggered_two_etf_sells_upgrade_to_green(self) -> None:
        histories, _ = _histories(
            {
                "00403A": {27: -250},
                "00991A": {29: -250},
            }
        )
        payload = build_consensus_payload(histories, {})
        self.assertEqual(1, len(payload["signals"]["selling"]))
        self.assertFalse(payload["signals"]["buying"])

    def test_second_manager_is_required_even_when_one_score_is_large(self) -> None:
        histories, dates = _histories({}, stock_id="6488")
        for index, session in enumerate(dates):
            histories["00403A"][session] = _day(
                session,
                {"6488": 100_000} if index == len(dates) - 1 else {},
            )
            histories["00981A"][session] = _day(session, {})
            histories["00991A"][session] = _day(session, {})
        payload = build_consensus_payload(histories, {})
        watch = payload["signals"]["watching"][0]
        self.assertGreaterEqual(watch["score"], 50)
        self.assertFalse(payload["signals"]["buying"])

    def test_consensus_strength_uses_weaker_manager_not_raw_sum(self) -> None:
        features = {
            "00403A": {
                "buy_days_10": 10,
                "sell_days_10": 0,
                "buy_strength_10": 30,
                "sell_strength_10": 0,
                "ewma_3": 2,
                "ewma_10": 2,
                "ewma_20": 2,
            },
            "00981A": {
                "buy_days_10": 1,
                "sell_days_10": 0,
                "buy_strength_10": 1,
                "sell_strength_10": 0,
                "ewma_3": 1,
                "ewma_10": 1,
                "ewma_20": 1,
            },
        }
        score, components = _score(
            features, ["00403A", "00981A"], 1
        )
        self.assertEqual(5, components["joint_persistence"])
        self.assertEqual(4, components["relative_strength"])
        self.assertLess(score, 55)

    def test_usual_action_multiple_is_not_the_significance_gate_multiple(self) -> None:
        dates = [
            (date(2026, 2, 1) + timedelta(days=index)).isoformat()
            for index in range(11)
        ]
        records = {}
        for index, session in enumerate(dates):
            flow = 0.4 if index == 10 else 0.2
            records[session] = {
                "2330": {
                    "00403A": {
                        "active_flow": flow,
                        "copyable": True,
                        "money_twd": flow / 100 * 10_000_000_000,
                    }
                }
            }
        assign_v4_action_scales(records, dates, ["00403A"])
        move = records[dates[-1]]["2330"]["00403A"]
        self.assertAlmostEqual(0.2, move["normal_action_flow"], places=5)
        self.assertAlmostEqual(0.12, move["significance_gate"], places=5)
        self.assertAlmostEqual(2.0, move["normal_action_multiple"], places=2)
        self.assertNotAlmostEqual(
            move["normal_action_multiple"],
            0.4 / move["significance_gate"],
            places=2,
        )

    def test_buy_baseline_never_pools_larger_sell_distribution(self) -> None:
        dates = [
            (date(2026, 2, 1) + timedelta(days=index)).isoformat()
            for index in range(17)
        ]
        records = {}
        for index, session in enumerate(dates):
            if index == len(dates) - 1:
                flow = 0.2
            else:
                flow = 0.1 if index % 2 == 0 else -1.0
            records[session] = {
                "2330": {
                    "00403A": {
                        "active_flow": flow,
                        "copyable": True,
                        "position_event": (
                            "increase" if flow > 0 else "decrease"
                        ),
                        "money_twd": abs(flow) / 100 * 10_000_000_000,
                    }
                }
            }
        assign_v4_action_scales(records, dates, ["00403A"])
        move = records[dates[-1]]["2330"]["00403A"]
        self.assertAlmostEqual(0.1, move["normal_action_flow"], places=5)
        self.assertAlmostEqual(2.0, move["normal_action_multiple"], places=2)
        self.assertEqual(
            "same_direction_expanded", move["v4_baseline_source"]
        )

    def test_maintenance_direction_uses_direction_standardized_actions(self) -> None:
        histories, _ = _histories(
            {"00403A": {20: 100, 21: 100, 22: -300}}
        )
        payload = build_consensus_payload(histories, {})
        latest = payload["series"]["2330"]["00403A"][-1]
        self.assertLess(latest["net_active_flow_10"], 0)
        self.assertEqual(-1, latest["raw_net_direction_10"])
        self.assertGreater(latest["net_ratio_10"], 0)
        self.assertEqual(1, latest["net_direction_10"])

    def test_watch_direction_flip_resets_age_instead_of_inheriting_it(self) -> None:
        histories, dates = _histories({}, stock_id="2449")
        for index, session in enumerate(dates):
            shares = (
                {"2449": 1_000}
                if index < 10 or index >= 15
                else {}
            )
            histories["00981A"][session] = _day(session, shares)
        payload = build_consensus_payload(histories, {})
        reentry = hydrate_board(payload, dates[15])["signals"]["watching"][0]
        self.assertEqual(1, reentry["direction"])
        transition = next(
            row
            for row in payload["transitions"]["2449"]
            if row["date"] == dates[15]
        )
        self.assertEqual(0, transition["watch_age"])
        self.assertIn("反手", transition["transition"])

    def test_fresh_same_direction_action_renews_watch_lifetime(self) -> None:
        histories, dates = _histories({}, stock_id="6488")
        for index, session in enumerate(dates):
            shares = 0
            if index >= 10:
                shares = 500
            if index >= 18:
                shares = 750
            histories["00991A"][session] = _day(
                session, {"6488": shares} if shares else {}
            )
        payload = build_consensus_payload(histories, {})
        renewed = hydrate_board(payload, dates[18])["signals"]["watching"][0]
        self.assertEqual(
            "高資訊觀察獲得新證據", renewed["transition"]
        )
        still_valid = hydrate_board(payload, dates[27])["signals"]["watching"]
        self.assertTrue(still_valid)

    def test_payload_keeps_backtest_ready_daily_state_history(self) -> None:
        histories, dates = _histories(
            {"00403A": {27: 250}, "00981A": {29: 250}}
        )
        payload = build_consensus_payload(histories, {})
        rows = payload["state_history"]["2330"]
        self.assertEqual(len(payload["dates"]), len(rows))
        self.assertEqual(dates[-1], rows[-1]["date"])
        self.assertTrue(
            all(row["state_days"] == 0 for row in rows if row["state"] == "none")
        )
        self.assertTrue(
            payload["data_quality"]["historical_signal_replay_ready"]
        )
        self.assertFalse(payload["data_quality"]["price_returns_embedded"])

    def test_full_history_mode_does_not_drop_old_backtest_sessions(self) -> None:
        histories, _ = _histories({}, sessions=270)
        live = build_consensus_payload(histories, {})
        full = build_consensus_payload(
            histories, {}, history_sessions=None
        )
        self.assertEqual(260, len(live["dates"]))
        self.assertEqual(269, len(full["dates"]))
        self.assertIsNone(
            full["data_quality"]["history_session_limit_applied"]
        )

    def test_confirmed_score_declines_each_quiet_session(self) -> None:
        histories, dates = _histories(
            {
                "00403A": {10: 250},
                "00981A": {10: 250},
            }
        )
        payload = build_consensus_payload(histories, {})
        trigger = hydrate_board(payload, dates[10])["signals"]["buying"][0]
        later = hydrate_board(payload, dates[15])["signals"]["buying"][0]
        self.assertGreater(trigger["score"], later["score"])
        self.assertGreater(
            trigger["valid_sessions_remaining"],
            later["valid_sessions_remaining"],
        )
        self.assertIn("freshness", later["score_components"])

    def test_watch_score_and_validity_decline_on_quiet_sessions(self) -> None:
        histories, dates = _histories({}, stock_id="6488")
        for index, session in enumerate(dates):
            histories["00991A"][session] = _day(
                session, {"6488": 500} if index >= 10 else {}
            )
        payload = build_consensus_payload(histories, {})
        trigger = hydrate_board(payload, dates[10])["signals"]["watching"][0]
        quiet = hydrate_board(payload, dates[12])["signals"]["watching"][0]
        self.assertGreater(trigger["score"], quiet["score"])
        self.assertEqual(
            trigger["valid_sessions_remaining"] - 2,
            quiet["valid_sessions_remaining"],
        )
        expired = hydrate_board(payload, dates[20])["signals"]["watching"]
        self.assertFalse(expired)

    def test_historical_board_replay_does_not_use_future_action(self) -> None:
        histories, dates = _histories(
            {
                "00403A": {27: 250},
                "00981A": {29: 250},
            }
        )
        payload = build_consensus_payload(histories, {})
        prior = hydrate_board(payload, dates[-2])
        self.assertFalse(prior["signals"]["buying"])
        latest = hydrate_board(payload, dates[-1])
        self.assertTrue(latest["signals"]["buying"])

    def test_concept_tags_are_never_interpreted(self) -> None:
        histories, _ = _histories(
            {"00403A": {28: 250}, "00981A": {29: 250}}
        )
        payload = build_consensus_payload(
            histories,
            {
                "2330": {
                    "name": "台積電",
                    "category": "IC-代工",
                    "concepts": [{"name": "禁止出現"}],
                }
            },
        )
        self.assertNotIn("禁止出現", str(payload["signals"]))
        self.assertFalse(payload["methodology"]["concepts_interpreted"])


if __name__ == "__main__":
    unittest.main()
