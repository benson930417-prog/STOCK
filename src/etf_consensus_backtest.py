"""No-lookahead portfolio backtest for ETF consensus V4 signals."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import floor
from typing import Any


@dataclass(frozen=True)
class BacktestConfig:
    initial_capital: float = 1_000_000.0
    max_positions: int = 5
    commission_rate: float = 0.001425
    sell_tax_rate: float = 0.003
    slippage_bps: float = 5.0


def _bars_by_symbol(price_payload: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    return {
        code: {str(bar["date"]): bar for bar in bars}
        for code, bars in (price_payload.get("symbols") or {}).items()
    }


def _next_bar_date(bars: dict[str, dict[str, Any]], after: str) -> str | None:
    return next((day for day in sorted(bars) if day > after), None)


def _signal_metadata(consensus: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for day, board in (consensus.get("boards") or {}).items():
        for lane in ("buying", "selling", "watching"):
            for card in board.get(lane) or []:
                result[(str(card.get("stock_id")), str(day))] = card
    return result


def _stock_names(consensus: dict[str, Any]) -> dict[str, str]:
    names: dict[str, str] = {}
    for board in (consensus.get("boards") or {}).values():
        for lane in ("buying", "selling", "watching"):
            for card in board.get(lane) or []:
                names[str(card.get("stock_id"))] = str(card.get("name") or card.get("stock_id"))
    return names


def _max_drawdown(values: list[float]) -> float:
    peak = 0.0
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        if peak:
            worst = min(worst, value / peak - 1.0)
    return worst


def run_backtest(
    consensus: dict[str, Any],
    price_payload: dict[str, Any],
    config: BacktestConfig,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """Enter/exit at the next available open after a disclosed V4 state change.

    Long only. A new ``buy`` state creates one entry order. Losing ``buy`` creates
    an exit order. Exits execute before entries, and no signal-day price is used.
    """
    if config.initial_capital <= 0 or config.max_positions < 1:
        raise ValueError("initial_capital and max_positions must be positive")
    bars = _bars_by_symbol(price_payload)
    benchmark = bars.get(str(price_payload.get("benchmark") or "0050"), {})
    if not benchmark:
        raise ValueError("0050 benchmark bars are required")
    market_end = _next_bar_date(benchmark, end_date) if end_date else None
    market_end = market_end or end_date
    all_days = [day for day in sorted(benchmark) if (not start_date or day >= start_date) and (not market_end or day <= market_end)]
    if not all_days:
        raise ValueError("No benchmark trading days in selected range")

    metadata = _signal_metadata(consensus)
    names = _stock_names(consensus)
    orders: dict[str, list[dict[str, Any]]] = {}
    for code, history in (consensus.get("state_history") or {}).items():
        symbol_bars = bars.get(code) or {}
        previous = "none"
        for row in history:
            signal_day = str(row.get("date"))
            if start_date and signal_day < start_date:
                continue
            if end_date and signal_day > end_date:
                break
            state = str(row.get("state") or "none")
            event = None
            if state == "buy" and previous != "buy":
                event = "buy"
            elif state != "buy" and previous == "buy":
                event = "sell"
            previous = state
            if not event:
                continue
            execution_day = _next_bar_date(symbol_bars, signal_day)
            if not execution_day:
                continue
            card = metadata.get((code, signal_day), {})
            orders.setdefault(execution_day, []).append(
                {
                    "side": event,
                    "symbol": code,
                    "signal_date": signal_day,
                    "score": float(row.get("score") or 0),
                    "tier": str(card.get("decision_tier") or "tracking"),
                    "transition": str(row.get("transition") or ""),
                }
            )

    cash = float(config.initial_capital)
    slot_budget = float(config.initial_capital) / config.max_positions
    holdings: dict[str, dict[str, Any]] = {}
    trades: list[dict[str, Any]] = []
    equity: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    last_close: dict[str, float] = {}
    slip = config.slippage_bps / 10_000.0

    for day in all_days:
        for symbol, symbol_bars in bars.items():
            bar = symbol_bars.get(day)
            if bar:
                last_close[symbol] = float(bar["close"])
        todays = orders.get(day) or []
        for order in [item for item in todays if item["side"] == "sell"]:
            symbol = order["symbol"]
            position = holdings.pop(symbol, None)
            bar = (bars.get(symbol) or {}).get(day)
            if not position or not bar:
                continue
            fill = float(bar["open"]) * (1.0 - slip)
            gross = position["shares"] * fill
            fee = gross * config.commission_rate
            tax = gross * config.sell_tax_rate
            cash += gross - fee - tax
            pnl = gross - fee - tax - position["cost"]
            trades.append(
                {
                    "symbol": symbol,
                    "name": names.get(symbol, symbol),
                    "entry_signal_date": position["signal_date"],
                    "entry_date": position["entry_date"],
                    "exit_signal_date": order["signal_date"],
                    "exit_date": day,
                    "shares": position["shares"],
                    "entry_price": position["fill"],
                    "exit_price": fill,
                    "pnl": pnl,
                    "return_pct": pnl / position["cost"] if position["cost"] else 0.0,
                    "status": "closed",
                }
            )

        entries = [item for item in todays if item["side"] == "buy"]
        entries.sort(key=lambda item: (item["tier"] == "core", item["score"]), reverse=True)
        for order in entries:
            symbol = order["symbol"]
            bar = (bars.get(symbol) or {}).get(day)
            if symbol in holdings or not bar:
                continue
            if len(holdings) >= config.max_positions:
                skipped.append({**order, "execution_date": day, "reason": "無空位"})
                continue
            fill = float(bar["open"]) * (1.0 + slip)
            shares = floor(min(slot_budget, cash) / (fill * (1.0 + config.commission_rate)))
            if shares < 1:
                skipped.append({**order, "execution_date": day, "reason": "現金不足"})
                continue
            gross = shares * fill
            fee = gross * config.commission_rate
            cost = gross + fee
            cash -= cost
            holdings[symbol] = {
                "shares": shares,
                "fill": fill,
                "cost": cost,
                "entry_date": day,
                "signal_date": order["signal_date"],
                "score": order["score"],
            }

        market_value = sum(pos["shares"] * last_close.get(symbol, pos["fill"]) for symbol, pos in holdings.items())
        equity.append({"date": day, "equity": cash + market_value, "cash": cash, "positions": len(holdings)})

    for symbol, position in holdings.items():
        mark = last_close.get(symbol, position["fill"])
        pnl = position["shares"] * mark - position["cost"]
        trades.append(
            {
                "symbol": symbol,
                "name": names.get(symbol, symbol),
                "entry_signal_date": position["signal_date"],
                "entry_date": position["entry_date"],
                "exit_signal_date": None,
                "exit_date": None,
                "shares": position["shares"],
                "entry_price": position["fill"],
                "exit_price": mark,
                "pnl": pnl,
                "return_pct": pnl / position["cost"] if position["cost"] else 0.0,
                "status": "open",
            }
        )

    first_benchmark = float(benchmark[all_days[0]]["close"])
    for row in equity:
        close = float(benchmark[row["date"]]["close"])
        row["strategy_return"] = row["equity"] / config.initial_capital - 1.0
        row["benchmark_return"] = close / first_benchmark - 1.0
    closed = [trade for trade in trades if trade["status"] == "closed"]
    final_equity = equity[-1]["equity"]
    metrics = {
        "final_equity": final_equity,
        "total_return": final_equity / config.initial_capital - 1.0,
        "benchmark_return": equity[-1]["benchmark_return"],
        "max_drawdown": _max_drawdown([row["equity"] for row in equity]),
        "closed_trades": len(closed),
        "open_positions": len(holdings),
        "win_rate": (sum(trade["pnl"] > 0 for trade in closed) / len(closed)) if closed else 0.0,
    }
    return {"metrics": metrics, "equity": equity, "trades": trades, "skipped": skipped}


def audit_latest_three_days(consensus: dict[str, Any], price_payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate exactly the latest three V4 sessions against Yuanta daily bars."""
    bars = _bars_by_symbol(price_payload)
    benchmark = bars.get(str(price_payload.get("benchmark") or "0050"), {})
    symbols = sorted(consensus.get("state_history") or {})
    rows: list[dict[str, Any]] = []
    auditable_dates = [day for day in (consensus.get("dates") or []) if day in benchmark]
    for signal_day in auditable_dates[-3:]:
        present = 0
        invalid = 0
        for symbol in symbols:
            bar = (bars.get(symbol) or {}).get(signal_day)
            if not bar:
                continue
            present += 1
            o, h, low, c = (float(bar[key]) for key in ("open", "high", "low", "close"))
            if low > min(o, c) or h < max(o, c) or low > h:
                invalid += 1
        bench = benchmark.get(signal_day) or {}
        rows.append(
            {
                "signal_date": signal_day,
                "next_trading_date": _next_bar_date(benchmark, signal_day) or "尚未發生",
                "covered_symbols": present,
                "expected_symbols": len(symbols),
                "invalid_ohlc": invalid,
                "0050_open": bench.get("open"),
                "0050_high": bench.get("high"),
                "0050_low": bench.get("low"),
                "0050_close": bench.get("close"),
                "passed": present == len(symbols) and invalid == 0 and bool(bench),
            }
        )
    return rows
