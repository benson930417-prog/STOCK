from __future__ import annotations

from datetime import date, timedelta
import unittest

from src.etf_consensus_v4 import (
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
