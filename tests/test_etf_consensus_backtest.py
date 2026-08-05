from src.etf_consensus_backtest import BacktestConfig, audit_latest_three_days, run_backtest


def _prices():
    days = ["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07"]
    def bars(values):
        return [{"date": day, "open": value, "high": value + 1, "low": value - 1, "close": value + .5, "volume": 1000} for day, value in zip(days, values)]
    return {"benchmark": "0050", "symbols": {"0050": bars([100, 101, 102, 103]), "2330": bars([50, 51, 55, 54])}}


def _consensus():
    return {
        "dates": ["2026-01-02", "2026-01-05", "2026-01-06"],
        "state_history": {"2330": [
            {"date": "2026-01-02", "state": "buy", "score": 70, "transition": "buy"},
            {"date": "2026-01-05", "state": "buy", "score": 65, "transition": "hold"},
            {"date": "2026-01-06", "state": "none", "score": 0, "transition": "exit"},
        ]},
        "boards": {"2026-01-02": {"buying": [{"stock_id": "2330", "name": "台積電", "decision_tier": "core"}], "selling": [], "watching": []}},
    }


def test_uses_next_session_open_and_exits_after_buy_ends():
    result = run_backtest(_consensus(), _prices(), BacktestConfig(initial_capital=100_000, max_positions=1, commission_rate=0, sell_tax_rate=0, slippage_bps=0))
    trade = result["trades"][0]
    assert trade["entry_signal_date"] == "2026-01-02"
    assert trade["entry_date"] == "2026-01-05"
    assert trade["entry_price"] == 51
    assert trade["exit_signal_date"] == "2026-01-06"
    assert trade["exit_date"] == "2026-01-07"
    assert trade["exit_price"] == 54


def test_latest_three_day_audit_is_exactly_three_and_valid():
    audit = audit_latest_three_days(_consensus(), _prices())
    assert len(audit) == 3
    assert all(row["passed"] for row in audit)
