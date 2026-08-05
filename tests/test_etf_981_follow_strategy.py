from src.etf_981_follow_strategy import build_981_follow_signal


def _day(date, units, shares):
    return {
        "date": date,
        "meta": {"outstanding_units": units, "fund_size": 1_000_000, "nav": 10},
        "holdings": [{"id": "2330", "name": "台積電", "shares": shares, "weight_pct": 10}],
    }


def test_981_signal_requires_real_and_flow_adjusted_buy_then_stops_immediately():
    history = {
        "2026-01-01": _day("2026-01-01", 1000, 100),
        "2026-01-02": _day("2026-01-02", 1000, 110),
        "2026-01-03": _day("2026-01-03", 1100, 121),  # mechanical scale only
        "2026-01-04": _day("2026-01-04", 1100, 125),
    }
    signal = build_981_follow_signal(history)
    rows = signal["state_history"]["2330"]
    assert [row["state"] for row in rows] == ["buy", "none", "buy"]
    assert rows[0]["transition"] == "00981A 開始主動加碼"
    assert rows[1]["transition"] == "00981A 本期未續買"
    assert len(signal["boards"]["2026-01-03"]["buying"]) == 0


def test_981_signal_rejects_raw_buy_when_active_allocation_falls():
    history = {
        "2026-01-01": _day("2026-01-01", 1000, 100),
        "2026-01-02": _day("2026-01-02", 2000, 150),
    }
    row = build_981_follow_signal(history)["state_history"]["2330"][0]
    assert row["raw_delta_shares"] > 0
    assert row["active_flow"] < 0
    assert row["state"] == "none"
