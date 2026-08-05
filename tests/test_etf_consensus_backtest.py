from src.etf_consensus_backtest import (
    BacktestConfig,
    audit_full_range,
    audit_latest_three_days,
    run_backtest,
)


def _prices(volume=1000):
    days = ["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07"]
    def bars(values):
        return [{"date": day, "open": value, "high": value + 1, "low": value - 1, "close": value + .5, "volume": volume} for day, value in zip(days, values)]
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


def _free_config(**kwargs):
    base = dict(initial_capital=100_000, max_positions=1, commission_rate=0, sell_tax_rate=0, slippage_bps=0, min_commission=0, odd_lot_slippage_bps=0)
    base.update(kwargs)
    return BacktestConfig(**base)


def test_uses_next_session_open_and_exits_after_buy_ends():
    result = run_backtest(_consensus(), _prices(), _free_config())
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


def test_cash_dividend_is_paid_to_open_positions_and_benchmark():
    actions = {"events": {
        "2330": [{"ex_date": "2026-01-06", "kind": "息", "cash_dividend": 2.0, "share_multiplier": 1.0}],
        "0050": [{"ex_date": "2026-01-06", "kind": "息", "cash_dividend": 5.0, "share_multiplier": 1.0}],
    }}
    plain = run_backtest(_consensus(), _prices(), _free_config())
    paid = run_backtest(_consensus(), _prices(), _free_config(), corporate_actions=actions)
    shares = paid["trades"][0]["shares"]
    assert paid["metrics"]["dividends_collected"] == 2.0 * shares
    assert paid["trades"][0]["pnl"] > plain["trades"][0]["pnl"]
    # A price-only benchmark understates 0050 by exactly the dividend it paid.
    assert paid["metrics"]["benchmark_return"] > paid["metrics"]["benchmark_price_return"]


def test_stock_dividend_increases_share_count():
    actions = {"events": {"2330": [
        {"ex_date": "2026-01-06", "kind": "權", "cash_dividend": 0.0, "share_multiplier": 1.1},
    ]}}
    result = run_backtest(_consensus(), _prices(), _free_config(), corporate_actions=actions)
    plain = run_backtest(_consensus(), _prices(), _free_config())
    assert result["trades"][0]["shares"] > plain["trades"][0]["shares"]


def test_open_position_is_valued_net_of_exit_costs():
    consensus = _consensus()
    consensus["state_history"]["2330"] = consensus["state_history"]["2330"][:2]
    config = BacktestConfig(initial_capital=100_000, max_positions=1, commission_rate=0.001425, sell_tax_rate=0.003, slippage_bps=5, min_commission=20)
    result = run_backtest(consensus, _prices(), config)
    trade = result["trades"][0]
    assert trade["status"] == "open"
    assert result["metrics"]["net_total_return"] < result["metrics"]["total_return"]


def test_missing_exit_bar_keeps_position_instead_of_deleting_it():
    prices = _prices()
    prices["symbols"]["2330"] = [bar for bar in prices["symbols"]["2330"] if bar["date"] != "2026-01-07"]
    result = run_backtest(_consensus(), _prices() | {"symbols": prices["symbols"]}, _free_config())
    trade = result["trades"][0]
    assert trade["status"] == "blocked"
    assert result["metrics"]["unsold_at_end"] == 1
    # The shares must still be worth something, not silently vanish.
    assert result["metrics"]["final_equity"] > 0


def test_blocked_entry_is_requeued_until_the_signal_dies():
    prices = _prices()
    prices["symbols"]["2454"] = [dict(bar, date=bar["date"]) for bar in prices["symbols"]["2330"]]
    consensus = _consensus()
    consensus["state_history"]["2454"] = [
        {"date": "2026-01-02", "state": "buy", "score": 90, "transition": "buy"},
        {"date": "2026-01-05", "state": "buy", "score": 90, "transition": "hold"},
        {"date": "2026-01-06", "state": "none", "score": 0, "transition": "exit"},
    ]
    queued = run_backtest(consensus, prices, _free_config(max_positions=1))
    assert any(item["reason"].startswith("無空位") for item in queued["skipped"])
    dropped = run_backtest(consensus, prices, _free_config(max_positions=1, requeue_missed_entries=False))
    assert queued["metrics"]["queued_entries"] >= dropped["metrics"]["queued_entries"]


def test_disclosure_seen_after_the_open_pushes_the_fill_out_a_session():
    disclosure = {"first_seen": {"2026-01-02": "2026-01-05T02:00:00Z"}}  # 10:00 Taipei
    result = run_backtest(_consensus(), _prices(), _free_config(), disclosure_times=disclosure)
    # 01-05 open had already happened when we saw it, so the fill moves to 01-06.
    assert result["trades"][0]["entry_date"] == "2026-01-06"


def test_volume_participation_caps_the_fill():
    result = run_backtest(_consensus(), _prices(volume=1), _free_config(max_volume_participation=0.001))
    assert result["trades"][0]["shares"] == 1


def test_full_range_audit_reports_coverage_and_disclosure():
    report = audit_full_range(
        _consensus(),
        _prices(),
        disclosure_times={"audit": [{"date": "2026-01-02", "after_market_close": False}]},
    )
    assert report["tradable_signal_dates"] == 3
    assert report["coverage_pct"] == 100.0
    assert report["disclosure_suspect"] == ["2026-01-02"]
    assert report["passed"] is False
