"""Flow-adjusted active-ETF intent change engine.

V1 answers which categories are strong.  V2 describes stock action lifecycles.
V3 deliberately answers only one question: *what manager intent changed now?*

The engine therefore:

1. removes mechanical holding scale caused by ETF creations/redemptions;
2. keeps only copyable share actions whose actual trade direction agrees with
   the flow-adjusted allocation direction;
3. judges each ETF/stock action against only preceding observations;
4. requires at least two independent ETFs to act significantly in the same
   direction on the same common session;
5. maintains a hidden buy/neutral/sell regime, but emits only new buy or sell
   consensus transitions and a separately labelled next-session confirmation.

Category is display context only.  Concept tags are never read.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import math
from statistics import median
from typing import Iterable


ETF_LABEL = {"00403A": "403", "00981A": "981", "00991A": "991"}
BASELINE_SESSIONS = 20
CONTEXT_SESSIONS = 20
FRESH_SESSIONS = 1
QUIET_RESTART_SESSIONS = 5
MIN_ACTIVE_FLOW = 0.02
MIN_CONSENSUS_ETFS = 2
REGULAR_EVIDENCE_GATE = 2.40
REVERSAL_EVIDENCE_GATE = 1.20
REGIME_DECAY = 0.84
REGIME_INPUT_SCALE = 2.40
REGIME_THRESHOLD = 0.35

BUY_LABELS = {
    "new_position": "形成建倉共識",
    "reentry_position": "形成重新建倉共識",
    "sell_to_buy": "賣後轉買",
    "buy_onset": "形成買方共識",
    "buy_restart": "沉寂後重新買進",
    "buy_acceleration": "買進重新加速",
    "buy_breadth_expansion": "買方共識擴散",
    "buy_followthrough": "買方共識延續",
}
SELL_LABELS = {
    "full_exit": "形成出清共識",
    "buy_to_sell": "買後轉賣",
    "sell_onset": "形成賣方共識",
    "sell_restart": "沉寂後重新賣出",
    "sell_acceleration": "賣出重新加速",
    "sell_breadth_expansion": "賣方共識擴散",
    "sell_followthrough": "賣方共識延續",
}
STRUCTURAL_TYPES = {"new_position", "reentry_position", "full_exit"}
STOCK_DISPLAY_NAMES = {
    "2308": "台達電",
    "2330": "台積電",
    "2344": "華邦電",
    "2345": "智邦",
    "2383": "台光電",
    "2408": "南亞科",
    "2492": "華新科",
    "3037": "欣興",
    "3665": "貿聯-KY",
    "6239": "力成",
    "6488": "環球晶",
    "6515": "穎崴",
    "6805": "富世達",
}


def _quantile(values: Iterable[float], q: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _display_name(stock_id: str, source_name: str) -> str:
    return STOCK_DISPLAY_NAMES.get(stock_id, source_name)


def disclosed_units(day: dict) -> tuple[float | None, str]:
    """Return exact units when disclosed, otherwise a marked NAV estimate."""
    meta = day.get("meta") or {}
    exact = float(meta.get("outstanding_units") or 0.0)
    if exact > 0:
        return exact, "exact"
    fund_size = float(meta.get("fund_size") or 0.0)
    nav = float(meta.get("nav") or 0.0)
    if fund_size > 0 and nav > 0:
        return fund_size / nav, "estimated"
    return None, "missing"


def _holding_map(day: dict) -> dict[str, dict]:
    return {
        str(row.get("id") or ""): row
        for row in day.get("holdings") or []
        if str(row.get("id") or "")
    }


def _position_event(
    previous: dict | None,
    current: dict | None,
    raw_delta: int,
) -> str:
    if previous is None and current is not None:
        return "new_position"
    if previous is not None and current is None:
        return "full_exit"
    if raw_delta > 0:
        return "increase"
    if raw_delta < 0:
        return "decrease"
    return "unchanged"


def flow_adjusted_moves(
    current_day: dict,
    previous_day: dict,
    *,
    etf: str,
    date: str,
    tags: dict,
) -> list[dict]:
    """Calculate active allocation residuals for one ETF disclosure pair."""
    current = _holding_map(current_day)
    previous = _holding_map(previous_day)
    current_units, current_source = disclosed_units(current_day)
    previous_units, previous_source = disclosed_units(previous_day)
    if current_units and previous_units:
        unit_scale = current_units / previous_units
    else:
        unit_scale = 1.0
    unit_quality = (
        "exact"
        if current_source == previous_source == "exact"
        else "estimated"
        if "missing" not in {current_source, previous_source}
        else "missing"
    )
    current_fund_size = float(
        (current_day.get("meta") or {}).get("fund_size") or 0.0
    )
    rows: list[dict] = []
    for stock_id in sorted(set(current) | set(previous)):
        current_holding = current.get(stock_id)
        previous_holding = previous.get(stock_id)
        current_shares = int(
            (current_holding or {}).get("shares") or 0
        )
        previous_shares = int(
            (previous_holding or {}).get("shares") or 0
        )
        raw_delta = current_shares - previous_shares
        expected_shares = (
            previous_shares * unit_scale if previous_holding else 0.0
        )
        active_delta = current_shares - expected_shares
        event = _position_event(previous_holding, current_holding, raw_delta)

        previous_weight = float(
            (previous_holding or {}).get("weight_pct") or 0.0
        )
        current_weight = float(
            (current_holding or {}).get("weight_pct") or 0.0
        )
        if previous_holding and expected_shares > 0:
            active_flow = active_delta / expected_shares * previous_weight
        elif current_holding:
            active_flow = current_weight
        else:
            active_flow = -previous_weight

        if current_holding and current_shares:
            raw_flow = raw_delta * current_weight / current_shares
        elif previous_holding and previous_shares:
            raw_flow = raw_delta * previous_weight / previous_shares
        else:
            raw_flow = 0.0

        # A flow-adjusted residual can oppose the actual share trade when a
        # fund grew/shrank.  That is useful allocation context, but it is not a
        # market action the user can copy.  Store it for audit while keeping it
        # out of the actionable event gate.
        structural = event in {"new_position", "full_exit"}
        copyable = structural or (
            raw_delta != 0
            and active_flow != 0
            and (raw_delta > 0) == (active_flow > 0)
        )
        source = current_holding or previous_holding or {}
        tag = tags.get(stock_id) or {}
        money_twd = (
            active_flow / 100.0 * current_fund_size
            if current_fund_size
            else None
        )
        rows.append(
            {
                "date": date,
                "etf": etf,
                "id": stock_id,
                "name": str(tag.get("name") or source.get("name") or stock_id),
                "category": str(tag.get("category") or "未分類"),
                "position_event": event,
                "raw_delta_shares": raw_delta,
                "expected_shares": round(expected_shares, 2),
                "active_delta_shares": round(active_delta, 2),
                "raw_flow": round(raw_flow, 6),
                "active_flow": round(active_flow, 6),
                "money_twd": round(money_twd, 0) if money_twd is not None else None,
                "unit_scale": round(unit_scale, 9),
                "unit_quality": unit_quality,
                "copyable": copyable,
            }
        )
    return rows


def build_move_store(
    histories: dict[str, dict],
    tags: dict,
) -> tuple[dict, dict[str, list[str]]]:
    """Return flow-adjusted moves keyed by date/stock/ETF."""
    records: dict[str, dict[str, dict[str, dict]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    dates_by_etf: dict[str, list[str]] = {}
    for etf, history in histories.items():
        dates = sorted(history)
        dates_by_etf[etf] = dates[1:]
        for previous_date, date in zip(dates, dates[1:]):
            for row in flow_adjusted_moves(
                history[date],
                history[previous_date],
                etf=etf,
                date=date,
                tags=tags,
            ):
                records[date][row["id"]][etf] = row
    return records, dates_by_etf


def _common_dates(dates_by_etf: dict[str, list[str]]) -> list[str]:
    sets = [set(dates) for dates in dates_by_etf.values() if dates]
    return sorted(set.intersection(*sets)) if sets else []


def assign_no_lookahead_thresholds(
    records: dict[str, dict[str, dict[str, dict]]],
    common_dates: list[str],
    etfs: list[str],
) -> None:
    """Attach a trailing robust action gate to every ETF/stock move."""
    stocks = sorted(
        {
            stock_id
            for date in common_dates
            for stock_id in records.get(date, {})
        }
    )
    for etf in etfs:
        for stock_id in stocks:
            prior: list[float] = []
            for date in common_dates:
                move = records.get(date, {}).get(stock_id, {}).get(etf)
                sample = prior[-BASELINE_SESSIONS:]
                p80 = _quantile(sample, 0.80)
                nonzero = [value for value in sample if value > 1e-9]
                typical = median(nonzero) if nonzero else 0.0
                threshold = max(MIN_ACTIVE_FLOW, p80)
                if move is not None:
                    magnitude = (
                        abs(float(move.get("active_flow") or 0.0))
                        if move.get("copyable")
                        else 0.0
                    )
                    structural = move.get("position_event") in {
                        "new_position",
                        "full_exit",
                    }
                    move["baseline_sessions"] = len(sample)
                    move["typical_flow_20d"] = round(typical, 6)
                    move["threshold"] = round(threshold, 6)
                    move["significance_ratio"] = round(
                        magnitude / threshold if threshold else 0.0,
                        2,
                    )
                    move["significant"] = bool(
                        move.get("copyable")
                        and (structural or magnitude >= threshold)
                    )
                prior.append(
                    abs(float(move.get("active_flow") or 0.0))
                    if move and move.get("copyable")
                    else 0.0
                )


def _regime_label(score: float) -> str:
    if score >= REGIME_THRESHOLD:
        return "buy"
    if score <= -REGIME_THRESHOLD:
        return "sell"
    return "neutral"


def _direction_strength(moves: list[dict]) -> tuple[float, int]:
    if not moves:
        return 0.0, 0
    base = sum(
        min(3.0, max(1.0, float(move.get("significance_ratio") or 0.0)))
        for move in moves
    )
    breadth = len({str(move.get("etf") or "") for move in moves})
    return base + max(0, breadth - 1) * 0.40, breadth


def _choose_direction(moves: list[dict]) -> dict | None:
    positive = [
        move
        for move in moves
        if move.get("significant")
        and float(move.get("active_flow") or 0.0) > 0
    ]
    negative = [
        move
        for move in moves
        if move.get("significant")
        and float(move.get("active_flow") or 0.0) < 0
    ]
    positive_strength, positive_breadth = _direction_strength(positive)
    negative_strength, negative_breadth = _direction_strength(negative)
    # V3 is a copy-the-consensus surface, not a single-manager alert feed.
    # A one-ETF action remains available in the backend records for audit, but
    # it may not influence the visible regime or create an event.  There are
    # deliberately no entry, exit, reversal, or extreme-size exceptions.
    if positive_breadth < MIN_CONSENSUS_ETFS:
        positive = []
        positive_strength = 0.0
        positive_breadth = 0
    if negative_breadth < MIN_CONSENSUS_ETFS:
        negative = []
        negative_strength = 0.0
        negative_breadth = 0
    if not positive and not negative:
        return None
    if positive and negative:
        stronger = max(positive_strength, negative_strength)
        weaker = min(positive_strength, negative_strength)
        # Mixed manager action is not a clean instruction.  Only keep a side
        # when the evidence clearly dominates.
        if stronger <= 0 or weaker / stronger >= 0.50:
            return None
    if positive_strength > negative_strength:
        return {
            "direction": 1,
            "moves": positive,
            "strength": positive_strength,
            "breadth": positive_breadth,
        }
    return {
        "direction": -1,
        "moves": negative,
        "strength": negative_strength,
        "breadth": negative_breadth,
    }


def _event_type(
    *,
    direction: int,
    chosen: dict,
    prior_regime: str,
    quiet_sessions: int | None,
    prior_event_direction: int,
    prior_event_index: int | None,
    prior_event_type: str | None,
    date_index: int,
    recent_same: list[dict],
    exited_etfs: set[str],
) -> tuple[str | None, str]:
    moves = list(chosen["moves"])
    new_etfs = {
        str(move["etf"])
        for move in moves
        if move.get("position_event") == "new_position"
    }
    exit_etfs = {
        str(move["etf"])
        for move in moves
        if move.get("position_event") == "full_exit"
    }
    if direction > 0 and new_etfs:
        if new_etfs & exited_etfs:
            return "reentry_position", "structural"
        return "new_position", "structural"
    if direction < 0 and exit_etfs:
        return "full_exit", "structural"
    if (
        prior_event_index is not None
        and date_index - prior_event_index == 1
        and prior_event_direction == direction
        and prior_event_type not in {"buy_followthrough", "sell_followthrough"}
    ):
        return (
            "buy_followthrough" if direction > 0 else "sell_followthrough",
            "followthrough",
        )
    if (direction > 0 and prior_regime == "sell") or (
        direction < 0 and prior_regime == "buy"
    ):
        return (
            "sell_to_buy" if direction > 0 else "buy_to_sell",
            "reversal",
        )
    if quiet_sessions is not None and quiet_sessions >= QUIET_RESTART_SESSIONS:
        return (
            "buy_restart" if direction > 0 else "sell_restart",
            "restart",
        )
    if prior_regime == "neutral":
        return "buy_onset" if direction > 0 else "sell_onset", "onset"

    recent_breadth = max(
        (int(row.get("breadth") or 0) for row in recent_same),
        default=0,
    )
    if int(chosen["breadth"]) > recent_breadth:
        return (
            "buy_breadth_expansion"
            if direction > 0
            else "sell_breadth_expansion",
            "breadth_expansion",
        )
    recent_strengths = [
        float(row.get("strength") or 0.0) for row in recent_same
    ]
    typical_strength = median(recent_strengths) if recent_strengths else 0.0
    if float(chosen["strength"]) >= max(
        REGULAR_EVIDENCE_GATE,
        typical_strength * 1.50,
    ):
        return (
            "buy_acceleration" if direction > 0 else "sell_acceleration",
            "acceleration",
        )
    return None, "continuing"


def _event_gate(event_type: str | None, strength: float) -> bool:
    if not event_type:
        return False
    if event_type in STRUCTURAL_TYPES:
        return True
    if event_type in {"sell_to_buy", "buy_to_sell"}:
        return strength >= REVERSAL_EVIDENCE_GATE
    if event_type in {"buy_followthrough", "sell_followthrough"}:
        return strength >= 1.0
    return strength >= REGULAR_EVIDENCE_GATE


def _event_reason(event_type: str, moves: list[dict]) -> str:
    def labels_for(rows: list[dict]) -> str:
        return "、".join(
            ETF_LABEL.get(str(move["etf"]), str(move["etf"]))
            for move in rows
        )

    labels = labels_for(moves)
    if event_type == "new_position":
        entrants = [
            move
            for move in moves
            if move.get("position_event") == "new_position"
        ]
        continuing = [move for move in moves if move not in entrants]
        if continuing:
            return (
                f"{labels_for(entrants)} 首次納入，"
                f"{labels_for(continuing)} 同步顯著買進"
            )
        return f"{labels} 同步首次把股票納入持股"
    if event_type == "reentry_position":
        return f"{labels} 形成重新買回共識"
    if event_type == "full_exit":
        exits = [
            move
            for move in moves
            if move.get("position_event") == "full_exit"
        ]
        continuing = [move for move in moves if move not in exits]
        if continuing:
            return (
                f"{labels_for(exits)} 完整出清，"
                f"{labels_for(continuing)} 同步顯著賣出"
            )
        return f"{labels} 同步將股票移出持股名單"
    if event_type == "sell_to_buy":
        return f"{labels} 從顯著賣方轉為顯著買方"
    if event_type == "buy_to_sell":
        return f"{labels} 從顯著買方轉為顯著賣方"
    if event_type.endswith("followthrough"):
        action = "買進" if event_type.startswith("buy") else "賣出"
        return f"前一交易日已形成共識，{labels} 本交易日仍顯著{action}"
    if event_type.endswith("breadth_expansion"):
        action = "買方" if event_type.startswith("buy") else "賣方"
        return f"新增 ETF 加入{action}，共識正在擴散"
    if event_type.endswith("acceleration"):
        action = "買進" if event_type.startswith("buy") else "賣出"
        return f"{labels} 的{action}強度重新高於近期慣常"
    if event_type.endswith("restart"):
        action = "買進" if event_type.startswith("buy") else "賣出"
        return f"沉寂至少 {QUIET_RESTART_SESSIONS} 個交易日後，{labels} 重新顯著{action}"
    action = "買進" if event_type.startswith("buy") else "賣出"
    return f"{labels} 本交易日同步形成顯著{action}共識"


def _trend(
    records: dict[str, dict[str, dict[str, dict]]],
    dates: list[str],
    stock_id: str,
    end_index: int,
) -> list[dict]:
    rows = []
    start = max(0, end_index - CONTEXT_SESSIONS + 1)
    for date in dates[start : end_index + 1]:
        moves = records.get(date, {}).get(stock_id, {}).values()
        active = [
            move for move in moves if move.get("copyable")
        ]
        rows.append(
            {
                "date": date,
                "flow": round(
                    sum(float(move.get("active_flow") or 0.0) for move in active),
                    6,
                ),
                "breadth": sum(
                    1 for move in active if move.get("significant")
                ),
            }
        )
    return rows


def _quality_label(moves: list[dict]) -> tuple[str, str]:
    if moves and all(move.get("unit_quality") == "exact" for move in moves):
        return "exact", "精確單位數"
    if any(move.get("unit_quality") == "missing" for move in moves):
        return "missing", "單位數不足"
    return "estimated", "歷史單位數估算"


def build_intent_payload(
    histories: dict[str, dict],
    tags: dict,
    *,
    as_of: str | None = None,
) -> dict:
    """Build V3 observations, hidden regimes, events, and two fresh lanes."""
    etfs = sorted(histories)
    records, dates_by_etf = build_move_store(histories, tags)
    common_dates = _common_dates(dates_by_etf)
    if as_of is not None:
        common_dates = [date for date in common_dates if date <= as_of]
    if not common_dates:
        raise ValueError("V3 requires at least one common ETF disclosure date")
    assign_no_lookahead_thresholds(records, common_dates, etfs)

    stock_ids = sorted(
        {
            stock_id
            for date in common_dates
            for stock_id in records.get(date, {})
        }
    )
    names: dict[str, str] = {}
    categories: dict[str, str] = {}
    for date in common_dates:
        for stock_id, by_etf in records.get(date, {}).items():
            if by_etf:
                example = next(iter(by_etf.values()))
                names[stock_id] = _display_name(
                    stock_id,
                    str(example.get("name") or stock_id),
                )
                categories[stock_id] = str(
                    example.get("category") or "未分類"
                )

    all_events: list[dict] = []
    regime_audit: dict[str, list[dict]] = {}
    for stock_id in stock_ids:
        regime_score = 0.0
        last_meaningful_index: int | None = None
        prior_event_index: int | None = None
        prior_event_direction = 0
        prior_event_type: str | None = None
        exited_etfs: set[str] = set()
        daily_evidence: list[dict] = []
        stock_regimes: list[dict] = []
        for date_index, date in enumerate(common_dates):
            all_moves = list(
                records.get(date, {}).get(stock_id, {}).values()
            )
            chosen = _choose_direction(all_moves)
            prior_regime = _regime_label(regime_score)
            quiet_sessions = (
                date_index - last_meaningful_index
                if last_meaningful_index is not None
                else None
            )
            event_type = None
            event_kind = ""
            if chosen:
                direction = int(chosen["direction"])
                selected_moves = list(chosen["moves"])
                recent_same = [
                    row
                    for row in daily_evidence[-CONTEXT_SESSIONS:]
                    if int(row.get("direction") or 0) == direction
                ]
                event_type, event_kind = _event_type(
                    direction=direction,
                    chosen=chosen,
                    prior_regime=prior_regime,
                    quiet_sessions=quiet_sessions,
                    prior_event_direction=prior_event_direction,
                    prior_event_index=prior_event_index,
                    prior_event_type=prior_event_type,
                    date_index=date_index,
                    recent_same=recent_same,
                    exited_etfs=exited_etfs,
                )
                strength = float(chosen["strength"])
                signed_input = direction * min(
                    1.5,
                    strength / REGIME_INPUT_SCALE,
                )
                regime_score = (
                    REGIME_DECAY * regime_score
                    + (1.0 - REGIME_DECAY) * signed_input
                )
                last_meaningful_index = date_index
                daily_evidence.append(
                    {
                        "date": date,
                        "direction": direction,
                        "strength": round(strength, 3),
                        "breadth": int(chosen["breadth"]),
                    }
                )
                if _event_gate(event_type, strength):
                    moves = selected_moves
                    quality, quality_label = _quality_label(moves)
                    etf_ids = [str(move["etf"]) for move in moves]
                    event = {
                        "stock_id": stock_id,
                        "name": names.get(stock_id, stock_id),
                        "category": categories.get(stock_id, "未分類"),
                        "signal_date": date,
                        "direction": direction,
                        "event_type": event_type,
                        "event_kind": event_kind,
                        "event_label": (
                            BUY_LABELS[event_type]
                            if direction > 0
                            else SELL_LABELS[event_type]
                        ),
                        "reason": _event_reason(event_type, moves),
                        "etfs": etf_ids,
                        "etf_label": "・".join(
                            ETF_LABEL.get(etf, etf) for etf in etf_ids
                        ),
                        "breadth": int(chosen["breadth"]),
                        "consensus_etfs": int(chosen["breadth"]),
                        "weakest_significance_ratio": round(
                            min(
                                float(
                                    move.get("significance_ratio") or 0.0
                                )
                                for move in moves
                            ),
                            2,
                        ),
                        "strength": round(strength, 3),
                        "data_quality": quality,
                        "data_quality_label": quality_label,
                        "estimated_money_yi": round(
                            sum(
                                float(move.get("money_twd") or 0.0)
                                for move in moves
                            )
                            / 100_000_000,
                            2,
                        ),
                        "evidence": [
                            {
                                "etf": str(move["etf"]),
                                "etf_label": ETF_LABEL.get(
                                    str(move["etf"]),
                                    str(move["etf"]),
                                ),
                                "action": (
                                    "買進"
                                    if float(move.get("active_flow") or 0.0) > 0
                                    else "賣出"
                                ),
                                "active_flow": round(
                                    float(move.get("active_flow") or 0.0),
                                    4,
                                ),
                                "significance_ratio": round(
                                    float(
                                        move.get("significance_ratio") or 0.0
                                    ),
                                    2,
                                ),
                                "position_event": str(
                                    move.get("position_event") or ""
                                ),
                                "raw_delta_shares": int(
                                    move.get("raw_delta_shares") or 0
                                ),
                                "unit_quality": str(
                                    move.get("unit_quality") or ""
                                ),
                            }
                            for move in moves
                        ],
                    }
                    all_events.append(event)
                    prior_event_index = date_index
                    prior_event_direction = direction
                    prior_event_type = event_type
                for move in selected_moves:
                    if move.get("position_event") == "new_position":
                        exited_etfs.discard(str(move["etf"]))
                    elif move.get("position_event") == "full_exit":
                        exited_etfs.add(str(move["etf"]))
            else:
                regime_score *= REGIME_DECAY
                for move in all_moves:
                    if move.get("position_event") == "full_exit":
                        exited_etfs.add(str(move["etf"]))
            stock_regimes.append(
                {
                    "date": date,
                    "score": round(regime_score, 4),
                    "state": _regime_label(regime_score),
                }
            )
        regime_audit[stock_id] = stock_regimes[-CONTEXT_SESSIONS:]

    latest_index = len(common_dates) - 1
    date_to_index = {date: index for index, date in enumerate(common_dates)}
    latest_by_stock: dict[tuple[str, int], dict] = {}
    for event in all_events:
        age = latest_index - date_to_index[event["signal_date"]]
        if age < 0 or age >= FRESH_SESSIONS:
            continue
        enriched = dict(event)
        enriched["age_sessions"] = age
        enriched["flow_trend_20d"] = _trend(
            records,
            common_dates,
            str(event["stock_id"]),
            date_to_index[event["signal_date"]],
        )
        if event["event_type"] in {"buy_followthrough", "sell_followthrough"}:
            timing = "昨日形成・本交易日仍確認"
            enriched["signal_phase"] = "confirmed"
        else:
            timing = "本交易日新形成"
            enriched["signal_phase"] = "new"
        enriched["timing_label"] = timing
        key = (str(event["stock_id"]), int(event["direction"]))
        previous = latest_by_stock.get(key)
        if previous is None or event["signal_date"] > previous["signal_date"]:
            latest_by_stock[key] = enriched

    fresh = list(latest_by_stock.values())
    buying = sorted(
        (event for event in fresh if event["direction"] > 0),
        key=lambda event: (
            -int(event.get("signal_phase") == "new"),
            -int(event["event_type"] in STRUCTURAL_TYPES),
            -int(event["breadth"]),
            -float(event["weakest_significance_ratio"]),
            event["stock_id"],
        ),
    )
    selling = sorted(
        (event for event in fresh if event["direction"] < 0),
        key=lambda event: (
            -int(event.get("signal_phase") == "new"),
            -int(event["event_type"] in STRUCTURAL_TYPES),
            -int(event["breadth"]),
            -float(event["weakest_significance_ratio"]),
            event["stock_id"],
        ),
    )
    exact_pairs = 0
    estimated_pairs = 0
    for date in common_dates:
        for by_etf in records.get(date, {}).values():
            for move in by_etf.values():
                if move.get("unit_quality") == "exact":
                    exact_pairs += 1
                elif move.get("unit_quality") == "estimated":
                    estimated_pairs += 1

    return {
        "schema_version": 4,
        "generated": datetime.now().astimezone().isoformat(timespec="seconds"),
        "as_of": common_dates[-1],
        "etfs": etfs,
        "dates": {
            "by_etf": dates_by_etf,
            "common": common_dates,
            "latest": common_dates[-1],
        },
        "methodology": {
            "signal": (
                "actual shares minus prior shares scaled by exact/estimated "
                "outstanding ETF units"
            ),
            "copyable_gate": (
                "actual share direction must agree with flow-adjusted direction"
            ),
            "minimum_same_direction_etfs": MIN_CONSENSUS_ETFS,
            "single_etf_exceptions": False,
            "baseline_sessions": BASELINE_SESSIONS,
            "minimum_active_flow_pct": MIN_ACTIVE_FLOW,
            "regular_evidence_gate": REGULAR_EVIDENCE_GATE,
            "visible_lanes": ["buying", "selling"],
            "hidden_states": ["buy", "neutral", "sell"],
            "concepts_interpreted": False,
        },
        "data_quality": {
            "exact_move_rows": exact_pairs,
            "estimated_move_rows": estimated_pairs,
            "historical_estimate": "fund_size / rounded NAV",
        },
        "signals": {"buying": buying, "selling": selling},
        "events": all_events,
        "regime_audit": regime_audit,
    }
