"""No-lookahead portfolio backtest for ETF consensus V4 signals.

Design notes that matter for reading the numbers:

* Yuanta ``GetKLine`` returns **raw** OHLC. Taiwan's dividend season sits inside
  the sample, so both holdings and the 0050 benchmark are paid their cash
  dividends here from the TWSE corporate-action table. Without that, every
  ex-dividend day is a pure unexplained price drop.
* Signals execute at the first session open that is both after the signal date
  and after we could actually have seen the disclosure.
* Open positions are reported at liquidation value (net of exit commission,
  tax and slippage), not at a costless mark.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import floor
from typing import Any

TAIPEI = timezone(timedelta(hours=8))
MARKET_OPEN = "09:00:00"


@dataclass(frozen=True)
class BacktestConfig:
    initial_capital: float = 1_000_000.0
    max_positions: int = 5
    commission_rate: float = 0.001425
    sell_tax_rate: float = 0.003
    slippage_bps: float = 5.0
    # Taiwan brokers charge a floor per ticket; it dominates small odd-lot fills.
    min_commission: float = 20.0
    # 1 = intraday odd-lot market (real in TW since 2020); 1000 = whole lots only.
    lot_size: int = 1
    # Odd lots match in a separate, thinner book than the regular session.
    odd_lot_slippage_bps: float = 20.0
    dividend_withholding_rate: float = 0.0
    # Size each slot off current equity so gains compound and losses shrink risk.
    compound_position_size: bool = True
    # A signal blocked by a full book stays queued while it remains a buy.
    requeue_missed_entries: bool = True
    # Cap a single fill's share of that session's traded volume.
    max_volume_participation: float = 0.05


def _bars_by_symbol(price_payload: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    return {
        code: {str(bar["date"]): bar for bar in bars}
        for code, bars in (price_payload.get("symbols") or {}).items()
    }


def _next_bar_date(bars: dict[str, dict[str, Any]], after: str) -> str | None:
    return next((day for day in sorted(bars) if day > after), None)


def _open_datetime(day: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{MARKET_OPEN}+08:00")


def _earliest_execution(
    bars: dict[str, dict[str, Any]], signal_day: str, first_seen_utc: str | None
) -> str | None:
    """First session open that is after the signal date *and* after disclosure.

    The signal-date rule alone assumes the snapshot reached us before the next
    open. When a disclosure timestamp exists we verify that instead of assuming
    it, and push the fill out by a session whenever it does not hold.
    """
    candidates = [day for day in sorted(bars) if day > signal_day]
    if first_seen_utc:
        try:
            seen = datetime.fromisoformat(
                str(first_seen_utc).replace("Z", "+00:00")
            ).astimezone(TAIPEI)
        except ValueError:
            seen = None
        if seen is not None:
            candidates = [day for day in candidates if _open_datetime(day) >= seen]
    return candidates[0] if candidates else None


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


def _actions_by_symbol(corporate_actions: dict[str, Any] | None) -> dict[str, dict[str, dict]]:
    """Index corporate actions as ``{symbol: {ex_date: event}}``."""
    events = (corporate_actions or {}).get("events") or {}
    return {
        str(symbol): {str(item["ex_date"]): item for item in records}
        for symbol, records in events.items()
    }


def _total_return_index(
    bars: dict[str, dict[str, Any]],
    days: list[str],
    actions: dict[str, dict],
) -> dict[str, float]:
    """Close-to-close index that reinvests cash dividends and absorbs splits.

    A price-only benchmark understates itself by exactly its dividend yield, so
    comparing a dividend-collecting strategy against one is not a fair contest.
    """
    index: dict[str, float] = {}
    level = 1.0
    previous_close: float | None = None
    for day in days:
        bar = bars.get(day)
        if not bar:
            if previous_close is not None:
                index[day] = level
            continue
        close = float(bar["close"])
        if previous_close is not None and previous_close > 0:
            event = actions.get(day) or {}
            cash = float(event.get("cash_dividend") or 0.0)
            multiplier = float(event.get("share_multiplier") or 1.0)
            level *= (close * multiplier + cash) / previous_close
        index[day] = level
        previous_close = close
    return index


def _episodes(
    consensus: dict[str, Any],
    bars: dict[str, dict[str, dict[str, Any]]],
    metadata: dict[tuple[str, str], dict[str, Any]],
    disclosure_times: dict[str, str],
    market_bars: dict[str, dict[str, Any]],
    start_date: str | None,
    end_date: str | None,
) -> list[dict[str, Any]]:
    """Turn per-symbol state history into discrete hold episodes.

    Pairing entry and exit up front is what lets a blocked entry stay queued for
    exactly as long as its signal is still live, and no longer.
    """
    episodes: list[dict[str, Any]] = []
    for code, history in (consensus.get("state_history") or {}).items():
        symbol_bars = bars.get(code) or {}
        previous = "none"
        current: dict[str, Any] | None = None
        for row in history:
            signal_day = str(row.get("date"))
            if start_date and signal_day < start_date:
                previous = str(row.get("state") or "none")
                continue
            if end_date and signal_day > end_date:
                break
            state = str(row.get("state") or "none")
            if state == "buy" and previous != "buy":
                card = metadata.get((code, signal_day), {})
                current = {
                    "symbol": code,
                    "entry_signal_date": signal_day,
                    "entry_execution_date": _earliest_execution(
                        symbol_bars, signal_day, disclosure_times.get(signal_day)
                    ),
                    "score": float(row.get("score") or 0),
                    "tier": str(card.get("decision_tier") or "tracking"),
                    "transition": str(row.get("transition") or ""),
                    "exit_signal_date": None,
                    "exit_execution_date": None,
                }
                episodes.append(current)
            elif state != "buy" and previous == "buy" and current is not None:
                current["exit_signal_date"] = signal_day
                execution = _earliest_execution(
                    symbol_bars, signal_day, disclosure_times.get(signal_day)
                )
                if execution is None:
                    # The symbol stops printing bars (halt, suspension, delisting).
                    # Fall back to the market calendar so the order stays live and
                    # visibly unfilled instead of being dropped on the floor.
                    execution = _earliest_execution(
                        market_bars, signal_day, disclosure_times.get(signal_day)
                    )
                current["exit_execution_date"] = execution
                current = None
            previous = state
    return [item for item in episodes if item["entry_execution_date"]]


def run_backtest(
    consensus: dict[str, Any],
    price_payload: dict[str, Any],
    config: BacktestConfig,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    corporate_actions: dict[str, Any] | None = None,
    disclosure_times: dict[str, Any] | None = None,
    buy_and_hold: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Enter/exit at the next tradable open after a disclosed V4 state change.

    Long only. Exits run before entries so a freed slot is reusable the same
    session. No signal-day price is ever read.
    """
    if config.initial_capital <= 0 or config.max_positions < 1:
        raise ValueError("initial_capital and max_positions must be positive")
    if config.lot_size < 1:
        raise ValueError("lot_size must be at least 1")
    bars = _bars_by_symbol(price_payload)
    benchmark_code = str(price_payload.get("benchmark") or "0050")
    benchmark = bars.get(benchmark_code, {})
    if not benchmark:
        raise ValueError("0050 benchmark bars are required")
    market_end = _next_bar_date(benchmark, end_date) if end_date else None
    market_end = market_end or end_date
    all_days = [
        day
        for day in sorted(benchmark)
        if (not start_date or day >= start_date) and (not market_end or day <= market_end)
    ]
    if not all_days:
        raise ValueError("No benchmark trading days in selected range")

    actions = _actions_by_symbol(corporate_actions)
    seen_map = {
        str(day): str(stamp)
        for day, stamp in ((disclosure_times or {}).get("first_seen") or {}).items()
    }
    metadata = _signal_metadata(consensus)
    names = _stock_names(consensus)
    episodes = _episodes(consensus, bars, metadata, seen_map, benchmark, start_date, end_date)

    cash = float(config.initial_capital)
    holdings: dict[str, dict[str, Any]] = {}
    trades: list[dict[str, Any]] = []
    equity: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    dividend_log: list[dict[str, Any]] = []
    last_close: dict[str, float] = {}
    base_slip = config.slippage_bps / 10_000.0
    odd_slip = config.odd_lot_slippage_bps / 10_000.0
    total_dividends = 0.0
    delayed_exits = 0

    def commission(gross: float) -> float:
        return max(gross * config.commission_rate, config.min_commission)

    def slip_for(shares: int) -> float:
        odd = config.lot_size == 1 and shares % 1000 != 0
        return base_slip + (odd_slip if odd else 0.0)

    pending_exit: dict[str, dict[str, Any]] = {}

    for day in all_days:
        for symbol, symbol_bars in bars.items():
            bar = symbol_bars.get(day)
            if bar:
                last_close[symbol] = float(bar["close"])

        # --- corporate actions on positions we actually hold -----------------
        for symbol, position in holdings.items():
            event = (actions.get(symbol) or {}).get(day)
            if not event:
                continue
            cash_per_share = float(event.get("cash_dividend") or 0.0)
            multiplier = float(event.get("share_multiplier") or 1.0)
            if cash_per_share:
                gross = position["shares"] * cash_per_share
                net = gross * (1.0 - config.dividend_withholding_rate)
                cash += net
                position["dividends"] = position.get("dividends", 0.0) + net
                total_dividends += net
                dividend_log.append(
                    {
                        "date": day,
                        "symbol": symbol,
                        "name": names.get(symbol, symbol),
                        "kind": event.get("kind"),
                        "per_share": cash_per_share,
                        "shares": position["shares"],
                        "amount": net,
                    }
                )
            if multiplier != 1.0:
                position["shares"] = int(floor(position["shares"] * multiplier))
                dividend_log.append(
                    {
                        "date": day,
                        "symbol": symbol,
                        "name": names.get(symbol, symbol),
                        "kind": event.get("kind"),
                        "per_share": 0.0,
                        "shares": position["shares"],
                        "amount": 0.0,
                    }
                )

        # --- exits before entries so a freed slot is reusable today ----------
        for symbol in list(holdings):
            episode = holdings[symbol]["episode"]
            exit_day = episode["exit_execution_date"]
            if not exit_day or exit_day > day:
                continue
            bar = (bars.get(symbol) or {}).get(day)
            if not bar:
                # Halted or suspended: keep the position and retry tomorrow.
                # Popping first and bailing on a missing bar would delete the
                # shares and the cash they represent.
                pending_exit[symbol] = episode
                delayed_exits += 1
                continue
            pending_exit.pop(symbol, None)
            position = holdings.pop(symbol)
            fill = float(bar["open"]) * (1.0 - slip_for(position["shares"]))
            gross = position["shares"] * fill
            fee = commission(gross)
            tax = gross * config.sell_tax_rate
            proceeds = gross - fee - tax
            cash += proceeds
            dividends = position.get("dividends", 0.0)
            pnl = proceeds + dividends - position["cost"]
            trades.append(
                {
                    "symbol": symbol,
                    "name": names.get(symbol, symbol),
                    "entry_signal_date": position["signal_date"],
                    "entry_date": position["entry_date"],
                    "exit_signal_date": episode["exit_signal_date"],
                    "exit_date": day,
                    "shares": position["shares"],
                    "entry_price": position["fill"],
                    "exit_price": fill,
                    "dividends": dividends,
                    "pnl": pnl,
                    "return_pct": pnl / position["cost"] if position["cost"] else 0.0,
                    "status": "closed",
                }
            )

        # --- entries ---------------------------------------------------------
        slot_equity = (
            equity[-1]["equity"] if (config.compound_position_size and equity) else config.initial_capital
        )
        slot_budget = slot_equity / config.max_positions
        candidates = [
            episode
            for episode in episodes
            if episode["entry_execution_date"]
            and (
                episode["entry_execution_date"] == day
                if not config.requeue_missed_entries
                else episode["entry_execution_date"] <= day
            )
            and not episode.get("filled")
            and episode["symbol"] not in holdings
            and (
                not episode["exit_execution_date"]
                or day < episode["exit_execution_date"]
            )
        ]
        candidates.sort(key=lambda item: (item["tier"] == "core", item["score"]), reverse=True)
        for episode in candidates:
            symbol = episode["symbol"]
            bar = (bars.get(symbol) or {}).get(day)
            if not bar:
                continue
            if len(holdings) >= config.max_positions:
                skipped.append(
                    {
                        **{key: episode[key] for key in ("symbol", "entry_signal_date", "score", "tier")},
                        "execution_date": day,
                        "reason": "無空位（仍在排隊）" if config.requeue_missed_entries else "無空位",
                    }
                )
                continue
            raw_fill = float(bar["open"])
            budget = min(slot_budget, cash)
            estimate = int(floor(budget / (raw_fill * (1.0 + base_slip + odd_slip) + 0.0)))
            shares = (estimate // config.lot_size) * config.lot_size
            volume_cap = int(
                floor(float(bar.get("volume") or 0) * 1000 * config.max_volume_participation)
            )
            if volume_cap and shares > volume_cap:
                shares = (volume_cap // config.lot_size) * config.lot_size
            if shares < 1:
                skipped.append(
                    {
                        **{key: episode[key] for key in ("symbol", "entry_signal_date", "score", "tier")},
                        "execution_date": day,
                        "reason": "現金不足" if budget > 0 else "無資金",
                    }
                )
                continue
            fill = raw_fill * (1.0 + slip_for(shares))
            gross = shares * fill
            fee = commission(gross)
            cost = gross + fee
            if cost > cash:
                shares = (int(floor((cash - config.min_commission) / (fill * (1.0 + config.commission_rate)))) // config.lot_size) * config.lot_size
                if shares < 1:
                    skipped.append(
                        {
                            **{key: episode[key] for key in ("symbol", "entry_signal_date", "score", "tier")},
                            "execution_date": day,
                            "reason": "現金不足",
                        }
                    )
                    continue
                gross = shares * fill
                fee = commission(gross)
                cost = gross + fee
            cash -= cost
            episode["filled"] = True
            holdings[symbol] = {
                "shares": shares,
                "fill": fill,
                "cost": cost,
                "entry_date": day,
                "signal_date": episode["entry_signal_date"],
                "score": episode["score"],
                "dividends": 0.0,
                "episode": episode,
            }

        market_value = sum(
            pos["shares"] * last_close.get(symbol, pos["fill"]) for symbol, pos in holdings.items()
        )
        equity.append(
            {
                "date": day,
                "equity": cash + market_value,
                "cash": cash,
                "positions": len(holdings),
            }
        )

    # --- open positions valued at what selling them would actually net -------
    liquidation_equity = equity[-1]["equity"]
    for symbol, position in holdings.items():
        mark = last_close.get(symbol, position["fill"])
        exit_fill = mark * (1.0 - slip_for(position["shares"]))
        gross = position["shares"] * exit_fill
        net = gross - commission(gross) - gross * config.sell_tax_rate
        dividends = position.get("dividends", 0.0)
        pnl = net + dividends - position["cost"]
        liquidation_equity -= position["shares"] * mark - net
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
                "exit_price": exit_fill,
                "dividends": dividends,
                "pnl": pnl,
                "return_pct": pnl / position["cost"] if position["cost"] else 0.0,
                "status": "blocked" if symbol in pending_exit else "open",
            }
        )

    benchmark_index = _total_return_index(benchmark, all_days, actions.get(benchmark_code) or {})
    hold_index: dict[str, float] = {}
    hold_label = None
    if buy_and_hold:
        hold_label = str(buy_and_hold.get("label") or buy_and_hold.get("symbol") or "")
        hold_bars = {
            str(day): {"close": float(close)}
            for day, close in (buy_and_hold.get("closes") or {}).items()
            if close
        }
        hold_index = _total_return_index(
            hold_bars, all_days, actions.get(str(buy_and_hold.get("symbol"))) or {}
        )

    first_benchmark = float(benchmark[all_days[0]]["close"])
    for row in equity:
        close = float(benchmark[row["date"]]["close"])
        row["strategy_return"] = row["equity"] / config.initial_capital - 1.0
        row["benchmark_price_return"] = close / first_benchmark - 1.0
        row["benchmark_return"] = benchmark_index.get(row["date"], 1.0) - 1.0
        if hold_index:
            row["buy_and_hold_return"] = hold_index.get(row["date"], 1.0) - 1.0

    closed = [trade for trade in trades if trade["status"] == "closed"]
    final_equity = equity[-1]["equity"]
    realized = sum(trade["pnl"] for trade in closed)
    unrealized = sum(trade["pnl"] for trade in trades if trade["status"] != "closed")
    metrics = {
        "final_equity": final_equity,
        "liquidation_equity": liquidation_equity,
        "total_return": final_equity / config.initial_capital - 1.0,
        "net_total_return": liquidation_equity / config.initial_capital - 1.0,
        "realized_pnl": realized,
        "unrealized_pnl": unrealized,
        # Ratio of magnitudes: realized/(realized+unrealized) flips sign and
        # blows past 100% whenever the two halves disagree.
        "realized_share": (
            abs(realized) / (abs(realized) + abs(unrealized))
            if (abs(realized) + abs(unrealized))
            else 0.0
        ),
        "dividends_collected": total_dividends,
        "benchmark_return": equity[-1]["benchmark_return"],
        "benchmark_price_return": equity[-1]["benchmark_price_return"],
        "buy_and_hold_return": equity[-1].get("buy_and_hold_return"),
        "buy_and_hold_label": hold_label,
        "max_drawdown": _max_drawdown([row["equity"] for row in equity]),
        "closed_trades": len(closed),
        "open_positions": len(holdings),
        "trading_days": len(all_days),
        "average_exposure": (
            sum(row["positions"] for row in equity) / (len(equity) * config.max_positions)
        )
        if equity
        else 0.0,
        "win_rate": (sum(trade["pnl"] > 0 for trade in closed) / len(closed)) if closed else 0.0,
        "queued_entries": len(skipped),
        "delayed_exits": delayed_exits,
        "unsold_at_end": len(pending_exit),
    }
    return {
        "metrics": metrics,
        "equity": equity,
        "trades": trades,
        "skipped": skipped,
        "dividends": dividend_log,
    }


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


def audit_full_range(
    consensus: dict[str, Any],
    price_payload: dict[str, Any],
    *,
    corporate_actions: dict[str, Any] | None = None,
    disclosure_times: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Scan the whole sample, not just three days.

    A three-day green tick reads as "the data is fine" while saying nothing
    about the other months, so this reports coverage, OHLC sanity, unexplained
    gaps and disclosure timing across every signal date in the run.
    """
    bars = _bars_by_symbol(price_payload)
    benchmark_code = str(price_payload.get("benchmark") or "0050")
    benchmark = bars.get(benchmark_code, {})
    symbols = sorted(consensus.get("state_history") or {})
    actions = _actions_by_symbol(corporate_actions)
    signal_dates = [str(day) for day in (consensus.get("dates") or [])]
    tradable = [day for day in signal_dates if day in benchmark]

    invalid_ohlc = 0
    missing_bars = 0
    expected_cells = 0
    for day in tradable:
        for symbol in symbols:
            expected_cells += 1
            bar = (bars.get(symbol) or {}).get(day)
            if not bar:
                missing_bars += 1
                continue
            o, h, low, c = (float(bar[key]) for key in ("open", "high", "low", "close"))
            if low > min(o, c) or h < max(o, c) or low > h:
                invalid_ohlc += 1

    # Overnight drops big enough to be a corporate action we have no record of.
    # Measured against the benchmark's own gap, otherwise a market-wide crash
    # day lights up every symbol at once and buries the real signal.
    benchmark_gap: dict[str, float] = {}
    benchmark_days = sorted(benchmark)
    for previous, current in zip(benchmark_days, benchmark_days[1:]):
        before = float(benchmark[previous]["close"])
        if before > 0:
            benchmark_gap[current] = float(benchmark[current]["open"]) / before - 1.0
    unexplained: list[dict[str, Any]] = []
    for symbol in symbols:
        symbol_bars = bars.get(symbol) or {}
        days = sorted(symbol_bars)
        for previous, current in zip(days, days[1:]):
            before = float(symbol_bars[previous]["close"])
            opening = float(symbol_bars[current]["open"])
            if before <= 0:
                continue
            gap = opening / before - 1.0
            excess = gap - benchmark_gap.get(current, 0.0)
            if excess <= -0.09 and current not in (actions.get(symbol) or {}):
                unexplained.append(
                    {
                        "symbol": symbol,
                        "date": current,
                        "gap_pct": round(gap * 100, 2),
                        "excess_pct": round(excess * 100, 2),
                    }
                )

    covered_actions = sum(
        1 for symbol in symbols for day in (actions.get(symbol) or {}) if day in benchmark
    )
    audit_rows = (disclosure_times or {}).get("audit") or []
    in_range = [row for row in audit_rows if str(row.get("date")) in set(signal_dates)]
    late = [row for row in in_range if not row.get("after_market_close")]
    return {
        "signal_dates": len(signal_dates),
        "tradable_signal_dates": len(tradable),
        "symbols": len(symbols),
        "expected_cells": expected_cells,
        "missing_bars": missing_bars,
        "coverage_pct": (
            (expected_cells - missing_bars) / expected_cells * 100.0 if expected_cells else 0.0
        ),
        "invalid_ohlc": invalid_ohlc,
        "corporate_actions_applied": covered_actions,
        "unexplained_gaps": unexplained,
        "disclosure_checked": len(in_range),
        "disclosure_suspect": [str(row.get("date")) for row in late],
        "passed": invalid_ohlc == 0 and not unexplained and not late,
    }
