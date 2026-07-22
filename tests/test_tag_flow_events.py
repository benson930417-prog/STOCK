from __future__ import annotations

from datetime import date, timedelta
import unittest

from scripts.build_tag_flow import flow_between
from src.tag_flow_events import build_event_snapshot
from src.ui.tag_flow_v2_tab import _event_card, _lane


ETFS = ["00403A", "00981A", "00991A"]


def _empty_fixture(session_count: int = 25) -> tuple[dict, list[str]]:
    dates = [
        (date(2026, 1, 1) + timedelta(days=index)).isoformat()
        for index in range(session_count)
    ]
    observations = [
        {
            "etf": etf,
            "date": session,
            "prev_date": session,
            "baseline": {"median": 0.10},
            "stocks": [],
        }
        for session in dates
        for etf in ETFS
    ]
    return (
        {
            "etfs": ETFS,
            "dates": {"by_etf": {etf: dates for etf in ETFS}},
            "observations": observations,
        },
        dates,
    )


def _add_move(
    data: dict,
    *,
    etf: str,
    session: str,
    stock_id: str,
    name: str,
    flow: float,
    position_event: str = "increase",
    category: str = "測試類股",
) -> None:
    observation = next(
        row
        for row in data["observations"]
        if row["etf"] == etf and row["date"] == session
    )
    observation["stocks"].append(
        {
            "id": stock_id,
            "name": name,
            "category": category,
            "concepts": ["不得參與判斷"],
            "flow": flow,
            "position_event": position_event,
        }
    )


class TagFlowEventTests(unittest.TestCase):
    def test_v2_cards_are_html_not_indented_markdown_code(self) -> None:
        event = {
            "age_sessions": 0,
            "event_label": "新建倉",
            "name": "測試股票",
            "category": "測試類股",
            "reason": "首次納入持股",
            "confirmation_label": "持股名單已改變",
        }

        card = _event_card(event, "buy")
        lane = _lane("剛開始買", "新建倉", [event], "buy")

        self.assertTrue(card.startswith('<div class="tfv2-card'))
        self.assertTrue(lane.startswith('<section class="tfv2-lane">'))
        self.assertNotIn("\n    <", card + lane)

    def test_tiny_routine_noise_is_hidden_but_tiny_new_position_surfaces(self) -> None:
        data, dates = _empty_fixture()
        for session in dates[-2:]:
            _add_move(
                data,
                etf="00403A",
                session=session,
                stock_id="NOISE",
                name="微小調整",
                flow=0.01,
            )
        _add_move(
            data,
            etf="00991A",
            session=dates[-1],
            stock_id="TRIAL",
            name="試單股票",
            flow=0.001,
            position_event="new_position",
        )

        snapshot = build_event_snapshot(data, ETFS)
        self.assertEqual(["試單股票"], [row["name"] for row in snapshot["buying"]])
        self.assertEqual("trial_position", snapshot["buying"][0]["event_type"])
        self.assertFalse(snapshot["selling"])

    def test_buy_to_sell_reversal_needs_prior_behaviour_and_confirmation(self) -> None:
        data, dates = _empty_fixture()
        for index in (4, 9, 14):
            _add_move(
                data,
                etf="00403A",
                session=dates[index],
                stock_id="REV",
                name="反手股票",
                flow=0.25,
            )
        for etf in ("00403A", "00981A"):
            _add_move(
                data,
                etf=etf,
                session=dates[-1],
                stock_id="REV",
                name="反手股票",
                flow=-0.20,
                position_event="decrease",
            )

        snapshot = build_event_snapshot(data, ETFS)
        self.assertEqual(1, len(snapshot["selling"]))
        event = snapshot["selling"][0]
        self.assertEqual("buy_to_sell", event["event_type"])
        self.assertEqual("breadth", event["confirmation"])
        self.assertEqual(2, event["breadth"])

    def test_strong_continuing_buy_is_hold_evidence_not_a_fresh_event(self) -> None:
        data, dates = _empty_fixture()
        for session in dates[-4:]:
            for etf in ("00403A", "00981A"):
                _add_move(
                    data,
                    etf=etf,
                    session=session,
                    stock_id="OLD",
                    name="延續買進",
                    flow=0.20,
                )

        snapshot = build_event_snapshot(data, ETFS)
        self.assertFalse(snapshot["buying"])
        self.assertFalse(snapshot["selling"])
        self.assertEqual(["延續買進"], [row["name"] for row in snapshot["holding"]])
        self.assertEqual("conviction_buy", snapshot["holding"][0]["event_type"])

    def test_reentry_after_full_exit_is_not_called_a_first_position(self) -> None:
        data, dates = _empty_fixture()
        _add_move(
            data,
            etf="00981A",
            session=dates[-10],
            stock_id="REENTRY",
            name="重新建倉股票",
            flow=-0.20,
            position_event="full_exit",
        )
        # The exit predates the ETFs' shared comparison window, matching the
        # real 981 南亞科 exit that occurred before 403 history began.
        data["dates"]["by_etf"]["00403A"].remove(dates[-10])
        _add_move(
            data,
            etf="00981A",
            session=dates[-1],
            stock_id="REENTRY",
            name="重新建倉股票",
            flow=0.10,
            position_event="new_position",
        )
        _add_move(
            data,
            etf="00991A",
            session=dates[-1],
            stock_id="REENTRY",
            name="重新建倉股票",
            flow=0.20,
        )

        snapshot = build_event_snapshot(data, ETFS)
        self.assertEqual(1, len(snapshot["buying"]))
        event = snapshot["buying"][0]
        self.assertEqual("reentry_position", event["event_type"])
        self.assertEqual("重新建倉", event["event_label"])
        self.assertIn("981 重新納入", event["reason"])
        self.assertIn("991 同日續買", event["reason"])

    def test_tiny_exit_is_cleanup_but_meaningful_exit_surfaces(self) -> None:
        data, dates = _empty_fixture()
        _add_move(
            data,
            etf="00991A",
            session=dates[-1],
            stock_id="TINY_EXIT",
            name="尾巴出清",
            flow=-0.001,
            position_event="full_exit",
        )
        _add_move(
            data,
            etf="00991A",
            session=dates[-1],
            stock_id="REAL_EXIT",
            name="完整出清",
            flow=-0.20,
            position_event="full_exit",
        )

        snapshot = build_event_snapshot(data, ETFS)
        self.assertEqual(["完整出清"], [row["name"] for row in snapshot["selling"]])
        self.assertEqual("full_exit", snapshot["selling"][0]["event_type"])

    def test_conflicting_etf_actions_are_not_copy_signals(self) -> None:
        data, dates = _empty_fixture()
        _add_move(
            data,
            etf="00403A",
            session=dates[-1],
            stock_id="MIXED",
            name="方向衝突",
            flow=0.30,
            position_event="new_position",
        )
        _add_move(
            data,
            etf="00981A",
            session=dates[-1],
            stock_id="MIXED",
            name="方向衝突",
            flow=-0.20,
            position_event="decrease",
        )

        snapshot = build_event_snapshot(data, ETFS)
        self.assertFalse(snapshot["buying"])
        self.assertFalse(snapshot["selling"])

    def test_flow_builder_marks_position_structure(self) -> None:
        base = {
            "meta": {"fund_size": 1_000_000_000},
            "holdings": [
                {"id": "ADD", "name": "加碼", "shares": 100, "weight_pct": 1.0},
                {"id": "TRIM", "name": "減碼", "shares": 100, "weight_pct": 1.0},
                {"id": "EXIT", "name": "出清", "shares": 100, "weight_pct": 1.0},
            ],
        }
        current = {
            "meta": {"fund_size": 1_000_000_000},
            "holdings": [
                {"id": "ADD", "name": "加碼", "shares": 120, "weight_pct": 1.2},
                {"id": "TRIM", "name": "減碼", "shares": 80, "weight_pct": 0.8},
                {"id": "NEW", "name": "建倉", "shares": 50, "weight_pct": 0.5},
            ],
        }

        moves = flow_between(current, base)
        self.assertEqual("increase", moves["ADD"]["position_event"])
        self.assertEqual("decrease", moves["TRIM"]["position_event"])
        self.assertEqual("new_position", moves["NEW"]["position_event"])
        self.assertEqual("full_exit", moves["EXIT"]["position_event"])


if __name__ == "__main__":
    unittest.main()
