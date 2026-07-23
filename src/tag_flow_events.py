"""Stock-level active-ETF action events for the buy/hold/sell radar.

The rotation view answers which category currently has pressure.  This engine
answers a narrower question: which disclosed stock action has just changed,
continued, approached a hold upgrade, or lost its hold evidence?  Every move
first clears a no-lookahead ETF+stock rolling significance gate.  It still
excludes chart windows, cash ranking, and category-level magnitude.  Category
is context only; concepts are ignored.
"""
from __future__ import annotations

from collections import defaultdict
from statistics import median


ETF_LABEL = {"00403A": "403", "00981A": "981", "00991A": "991"}
CONTEXT_SESSIONS = 20
IDLE_SESSIONS = 5
FRESH_SESSIONS = 2
MIN_NORMALIZED_FLOW = 0.02
MEDIAN_FRACTION = 0.35
STOCK_MEDIAN_FRACTION = 0.60
MIN_CONTEXT_NET = 0.05
MIN_CONTEXT_ACTION_DAYS = 2
CONVICTION_SESSIONS = 10
MIN_CONVICTION_BUY_DAYS = 4
MIN_CONVICTION_NET = 0.20
MIN_CONVICTION_ETFS = 2
MIN_STOCK_BASELINE_SAMPLES = 2

STOCK_DISPLAY_NAMES = {
    "2308": "台達電",
    "2330": "台積電",
    "2344": "華邦電",
    "2345": "智邦",
    "2383": "台光電",
    "2408": "南亞科",
    "2492": "華新科",
    "3037": "欣興",
    "3711": "日月光投控",
    "6239": "力成",
    "6515": "穎崴",
}

BUY_EVENT_LABELS = {
    "new_position": "新建倉",
    "trial_position": "試單建倉",
    "reentry_position": "重新建倉",
    "sell_to_buy": "賣後轉買",
    "restart_buy": "沉寂後開買",
}
SELL_EVENT_LABELS = {
    "full_exit": "完全出清",
    "buy_to_sell": "買後轉賣",
    "restart_sell": "沉寂後開賣",
}

STRUCTURAL_EVENT_TYPES = {
    "new_position",
    "trial_position",
    "reentry_position",
    "full_exit",
}
REVERSAL_EVENT_TYPES = {"sell_to_buy", "buy_to_sell"}


def _display_name(stock_id: str, source_name: str) -> str:
    return STOCK_DISPLAY_NAMES.get(stock_id, source_name)


def _short_date(date: str) -> str:
    return date[5:].replace("-", "/")


def _shared_dates(data: dict, etfs: list[str]) -> list[str]:
    by_etf = data.get("dates", {}).get("by_etf", {})
    sets = [set(by_etf.get(etf, [])) for etf in etfs]
    if not sets or any(not values for values in sets):
        return []
    return sorted(set.intersection(*sets))


def _move_threshold(observation: dict) -> float:
    median = float(observation.get("baseline", {}).get("median") or 0.0)
    return max(MIN_NORMALIZED_FLOW, median * MEDIAN_FRACTION)


def _assign_stock_thresholds(
    records: dict[str, dict[str, dict[str, dict]]],
    dates: list[str],
) -> None:
    """Attach a no-lookahead, ETF+stock significance gate to every move.

    A 981 trade must not be judged against 403's size, and a routine large
    rebalance in one stock must not look exceptional merely because another
    stock usually moves less.  For each ETF+stock+direction we therefore use
    the preceding ten common sessions.  When that exact history is too sparse,
    we fall back to the same stock's opposite-direction history, then to the
    existing ETF-wide daily baseline.
    """
    for stock_dates in records.values():
        history: dict[str, list[tuple[int, int, float]]] = defaultdict(list)
        for date_index, date in enumerate(dates):
            for etf, move in stock_dates.get(date, {}).items():
                flow = float(move.get("flow") or 0.0)
                direction = 1 if flow > 0 else -1 if flow < 0 else 0
                recent = [
                    (prior_direction, amount)
                    for prior_index, prior_direction, amount in history[etf]
                    if date_index - prior_index <= CONVICTION_SESSIONS
                ]
                same_direction = [
                    amount
                    for prior_direction, amount in recent
                    if prior_direction == direction
                ]
                any_direction = [amount for _, amount in recent]
                fallback = float(
                    move.get("fallback_threshold") or MIN_NORMALIZED_FLOW
                )
                if len(same_direction) >= MIN_STOCK_BASELINE_SAMPLES:
                    typical = float(median(same_direction))
                    source = "same_direction"
                elif len(any_direction) >= MIN_STOCK_BASELINE_SAMPLES:
                    typical = float(median(any_direction))
                    source = "stock_all_directions"
                else:
                    typical = fallback / MEDIAN_FRACTION
                    source = "etf_fallback"
                fraction = (
                    STOCK_MEDIAN_FRACTION
                    if source != "etf_fallback"
                    else MEDIAN_FRACTION
                )
                threshold = max(MIN_NORMALIZED_FLOW, typical * fraction)
                move["typical_flow_10d"] = round(typical, 4)
                move["threshold"] = round(threshold, 4)
                move["threshold_source"] = source
                move["significant"] = abs(flow) >= threshold
                move["significance_ratio"] = round(
                    abs(flow) / threshold if threshold else 0.0, 2
                )
                if direction:
                    history[etf].append(
                        (date_index, direction, abs(flow))
                    )


def _candidate(
    stock_dates: dict[str, dict[str, dict]],
    dates: list[str],
    index: int,
    etf_count: int,
) -> dict | None:
    if index < 0 or index >= len(dates):
        return None
    moves = stock_dates.get(dates[index], {})
    qualified: dict[str, dict] = {}
    for etf, move in moves.items():
        event = move.get("position_event")
        significant = bool(move.get("significant"))
        # A newly disclosed position is informative even when it begins as a
        # tiny test allocation.  Tiny full exits are usually residue cleanup,
        # so exits still have to clear the ordinary significance gate.
        if event == "new_position" or significant:
            qualified[etf] = move
    if not qualified:
        return None

    new_etfs = [
        etf for etf, move in qualified.items()
        if move.get("position_event") == "new_position"
    ]
    exit_etfs = [
        etf for etf, move in qualified.items()
        if move.get("position_event") == "full_exit"
    ]
    # Simultaneous entry and exit by different ETFs is a disagreement, not a
    # copyable directional event.
    if new_etfs and exit_etfs:
        return None

    positive_etfs = [
        etf for etf, move in qualified.items()
        if float(move.get("flow") or 0.0) > 0
    ]
    negative_etfs = [
        etf for etf, move in qualified.items()
        if float(move.get("flow") or 0.0) < 0
    ]
    if positive_etfs and negative_etfs:
        # Conflicting ETF actions can inform category context, but there is no
        # clean stock action to copy, so the action radar deliberately stays silent.
        return None

    score = sum(float(move.get("flow") or 0.0) for move in qualified.values()) / max(
        1, etf_count
    )
    direction = 1 if score > 0 else -1 if score < 0 else 0
    if not direction:
        return None
    aligned = positive_etfs if direction > 0 else negative_etfs
    if not aligned:
        return None

    structural_etfs = new_etfs if direction > 0 else exit_etfs
    strongest = max(
        (qualified[etf] for etf in aligned),
        key=lambda move: abs(float(move.get("flow") or 0.0)),
    )
    trial_position = bool(new_etfs) and all(
        abs(float(qualified[etf].get("flow") or 0.0))
        < float(qualified[etf].get("threshold") or MIN_NORMALIZED_FLOW)
        for etf in new_etfs
    )
    return {
        "direction": direction,
        "score": round(score, 4),
        "breadth": len(aligned),
        "etfs": aligned,
        "new_etfs": new_etfs,
        "exit_etfs": exit_etfs,
        "structural": bool(structural_etfs),
        "trial_position": trial_position,
        "name": _display_name(
            str(strongest.get("id") or ""),
            str(strongest.get("name") or strongest.get("id") or ""),
        ),
        "stock_id": str(strongest.get("id") or ""),
        "category": str(strongest.get("category") or "未分類"),
        "threshold_details": [
            {
                "etf": etf,
                "flow": round(float(qualified[etf].get("flow") or 0.0), 4),
                "threshold": round(
                    float(
                        qualified[etf].get("threshold")
                        or MIN_NORMALIZED_FLOW
                    ),
                    4,
                ),
                "typical_flow_10d": round(
                    float(qualified[etf].get("typical_flow_10d") or 0.0),
                    4,
                ),
                "source": str(
                    qualified[etf].get("threshold_source") or "etf_fallback"
                ),
                "structural_exception": bool(
                    qualified[etf].get("position_event") == "new_position"
                    and not qualified[etf].get("significant")
                ),
            }
            for etf in aligned
        ],
    }


def _confirmation(candidates: list[dict | None], index: int) -> dict | None:
    current = candidates[index] if 0 <= index < len(candidates) else None
    if not current:
        return None
    previous = candidates[index - 1] if index > 0 else None
    if current["structural"]:
        method = "structural"
        start_index = index
    elif current["breadth"] >= 2:
        method = "breadth"
        start_index = index
    elif previous and previous["direction"] == current["direction"]:
        method = "persistence"
        start_index = index - 1
    else:
        return None
    return {**current, "confirmation": method, "start_index": start_index}


def _prior_context(
    candidates: list[dict | None], start_index: int
) -> tuple[float, int, int, bool]:
    context = candidates[max(0, start_index - CONTEXT_SESSIONS) : start_index]
    prior_net = sum(float(item["score"]) for item in context if item)
    buy_days = sum(bool(item and item["direction"] > 0) for item in context)
    sell_days = sum(bool(item and item["direction"] < 0) for item in context)
    idle_context = candidates[max(0, start_index - IDLE_SESSIONS) : start_index]
    idle = bool(idle_context) and not any(idle_context)
    return round(prior_net, 4), buy_days, sell_days, idle


def _flow_trend(
    stock_dates: dict[str, dict[str, dict]], dates: list[str]
) -> list[dict]:
    """Return a fixed 20-session, fund-size-normalized stock-flow shape.

    Each bar sums the three ETFs' ActiveWeight-style flows for that common
    session.  Absolute cash is deliberately excluded so a larger ETF cannot
    dominate merely because its fund size is larger.
    """
    rows = []
    for date in dates[-CONTEXT_SESSIONS:]:
        moves = stock_dates.get(date, {})
        flow = sum(float(move.get("flow") or 0.0) for move in moves.values())
        breadth = sum(
            abs(float(move.get("flow") or 0.0)) > 1e-9
            for move in moves.values()
        )
        rows.append(
            {
                "date": date,
                "flow": round(flow, 4),
                "breadth": breadth,
            }
        )
    return rows


def _prior_opposite_range(
    candidates: list[dict | None],
    dates: list[str],
    start_index: int,
    direction: int,
) -> tuple[str, str] | None:
    start = max(0, start_index - CONTEXT_SESSIONS)
    opposite_dates = [
        dates[index]
        for index in range(start, start_index)
        if candidates[index] and candidates[index]["direction"] == -direction
    ]
    if not opposite_dates:
        return None
    return opposite_dates[0], opposite_dates[-1]


def _event_type(
    confirmed: dict,
    prior_net: float,
    buy_days: int,
    sell_days: int,
    idle: bool,
) -> str | None:
    if confirmed["new_etfs"]:
        if confirmed.get("reentry_exit_dates"):
            return "reentry_position"
        return "trial_position" if confirmed["trial_position"] else "new_position"
    if confirmed["exit_etfs"]:
        return "full_exit"
    if (
        confirmed["direction"] > 0
        and prior_net <= -MIN_CONTEXT_NET
        and sell_days >= MIN_CONTEXT_ACTION_DAYS
    ):
        return "sell_to_buy"
    if (
        confirmed["direction"] < 0
        and prior_net >= MIN_CONTEXT_NET
        and buy_days >= MIN_CONTEXT_ACTION_DAYS
    ):
        return "buy_to_sell"
    if idle:
        return "restart_buy" if confirmed["direction"] > 0 else "restart_sell"
    return None


def _confirmation_label(event: dict, etf_count: int) -> str:
    method = event["confirmation"]
    if method == "structural":
        return "持股名單已改變"
    if method == "breadth":
        return f"{event['breadth']}/{etf_count} ETF 同步"
    return "同方向連續 2 個交易日"


def _qualification(event: dict, etf_count: int) -> dict | None:
    """Explain why an event is strong enough to show, or reject it.

    Ordinary stock actions need at least two ETFs moving in the same direction.
    A single ETF is allowed only when the disclosed holding list changed, or
    when an actual reversal persisted for two consecutive common sessions.
    Continuing-conviction evidence is a separate lane.  A fresh/active strong
    hold still needs at least two ETFs during its ten-session window.  A
    just-cooled hold remains visible even when one ETF's older evidence has
    rolled out; that transition is context, not a fresh buy or sell signal.
    """
    event_type = str(event.get("event_type") or "")
    breadth = int(event.get("breadth") or 0)
    if event_type in {
        "conviction_buy",
        "conviction_watch",
        "conviction_downgrade",
    }:
        if event_type != "conviction_downgrade" and breadth < MIN_CONVICTION_ETFS:
            return None
        labels = {
            "conviction_buy": "續抱",
            "conviction_watch": "升級觀察",
            "conviction_downgrade": "續抱降溫",
        }
        suffix = (
            f"{breadth}檔仍有證據"
            if event_type == "conviction_downgrade"
            else f"{breadth}檔"
        )
        return {
            "qualification_kind": "continuation",
            "qualification_label": f"{labels[event_type]}（{suffix}）",
        }
    if breadth >= 2:
        return {
            "qualification_kind": "consensus",
            "qualification_label": f"共識（{breadth}/{etf_count}）",
        }
    if breadth != 1:
        return None
    if event_type in STRUCTURAL_EVENT_TYPES:
        exception = "建倉" if event.get("direction", 0) > 0 else "出清"
        return {
            "qualification_kind": "exception",
            "qualification_label": f"1/{etf_count} {exception}例外",
        }
    if (
        event_type in REVERSAL_EVENT_TYPES
        and event.get("confirmation") == "persistence"
    ):
        return {
            "qualification_kind": "exception",
            "qualification_label": f"1/{etf_count} 反轉2日",
        }
    return None


def _evidence_parts(event: dict) -> list[str]:
    """Return compact, reusable evidence facts for both web and LINE."""
    event_type = str(event.get("event_type") or "")
    if event_type == "reentry_position":
        parts = [
            f"{ETF_LABEL.get(etf, etf)} 重納"
            for etf in event.get("new_etfs", [])
        ]
        parts.extend(
            f"{ETF_LABEL.get(etf, etf)} 續買"
            for etf in event.get("etfs", [])
            if etf not in event.get("new_etfs", [])
        )
        return parts or ["曾出清後重納"]
    if event_type in {"new_position", "trial_position"}:
        suffix = "小額新納入" if event_type == "trial_position" else "新納入"
        return [
            f"{ETF_LABEL.get(etf, etf)} {suffix}"
            for etf in event.get("new_etfs", []) or event.get("etfs", [])
        ]
    if event_type == "full_exit":
        return [
            f"{ETF_LABEL.get(etf, etf)} 移除持股"
            for etf in event.get("exit_etfs", []) or event.get("etfs", [])
        ]
    if event_type == "sell_to_buy":
        return ["先前明顯減碼", "現在轉為買進"]
    if event_type == "buy_to_sell":
        return ["先前明顯加碼", "現在轉為賣出"]
    if event_type == "restart_buy":
        return ["沉寂至少5日", "現在重新買進"]
    if event_type == "restart_sell":
        return ["沉寂至少5日", "現在重新賣出"]
    if event_type == "conviction_buy":
        return [
            f"10日{int(event.get('buy_days') or 0)}買・"
            f"{int(event.get('sell_days') or 0)}賣",
            str(event.get("latest_action_label") or "續抱條件仍成立"),
        ]
    if event_type == "conviction_watch":
        return [
            f"10日{int(event.get('buy_days') or 0)}買・"
            f"{int(event.get('sell_days') or 0)}賣",
            str(event.get("progress_label") or "接近續抱門檻"),
        ]
    if event_type == "conviction_downgrade":
        return [
            f"10日{int(event.get('buy_days') or 0)}買・"
            f"{int(event.get('sell_days') or 0)}賣",
            str(
                event.get("latest_action_label")
                or "加碼動能降溫・尚無顯著賣出"
            ),
        ]
    return [str(event.get("event_label") or "訊號已成立")]


def _display_metadata(event: dict, etf_count: int) -> dict | None:
    qualification = _qualification(event, etf_count)
    if not qualification:
        return None
    etf_labels = [ETF_LABEL.get(etf, etf) for etf in event.get("etfs", [])]
    return {
        **qualification,
        "etf_label": "・".join(etf_labels) or "未提供",
        "evidence_parts": _evidence_parts(event),
        "significance_label": _significance_label(event),
    }


def _significance_label(event: dict) -> str:
    details = list(event.get("threshold_details") or [])
    if any(row.get("structural_exception") for row in details):
        return "持股名單改變；允許小額建倉例外"
    if details:
        stock_specific = sum(
            row.get("source") != "etf_fallback" for row in details
        )
        if stock_specific == len(details):
            return "各 ETF 均高於此股近10日慣常動作門檻"
        return "均通過逐 ETF 門檻；歷史不足者採 ETF 基準"
    if str(event.get("event_type") or "").startswith("conviction_"):
        return "只計入已通過逐 ETF 近10日門檻的動作"
    return "已通過逐 ETF 顯著性門檻"


def _reason(event: dict) -> str:
    labels = [ETF_LABEL.get(etf, etf) for etf in event["etfs"]]
    etf_text = "、".join(labels)
    event_type = event["event_type"]
    if event_type == "reentry_position":
        reentries = []
        for etf, exit_date in event["reentry_exit_dates"].items():
            label = ETF_LABEL.get(etf, etf)
            reentries.append(f"{label} 重新納入（{_short_date(exit_date)} 曾出清）")
        continuing = [etf for etf in event["etfs"] if etf not in event["new_etfs"]]
        if continuing:
            labels = "、".join(ETF_LABEL.get(etf, etf) for etf in continuing)
            reentries.append(f"{labels} 同日續買")
        return "；".join(reentries)
    if event_type in {"new_position", "trial_position"}:
        entry_labels = [ETF_LABEL.get(etf, etf) for etf in event["new_etfs"]]
        suffix = "，先視為試單" if event_type == "trial_position" else ""
        continuing = [etf for etf in event["etfs"] if etf not in event["new_etfs"]]
        continuation = ""
        if continuing:
            labels = "、".join(ETF_LABEL.get(etf, etf) for etf in continuing)
            continuation = f"；{labels} 同日續買"
        return f"{'、'.join(entry_labels)} 新納入持股{suffix}{continuation}"
    if event_type == "full_exit":
        exit_labels = [ETF_LABEL.get(etf, etf) for etf in event["exit_etfs"]]
        return f"{'、'.join(exit_labels)} 已從持股名單移除"
    if event_type == "sell_to_buy":
        start, end = event["prior_opposite_range"]
        period = (
            _short_date(start)
            if start == end
            else f"{_short_date(start)}–{_short_date(end)}"
        )
        return f"{etf_text} 剛轉為買進；{period} 曾明顯減碼"
    if event_type == "buy_to_sell":
        start, end = event["prior_opposite_range"]
        period = (
            _short_date(start)
            if start == end
            else f"{_short_date(start)}–{_short_date(end)}"
        )
        return f"{etf_text} 剛轉為賣出；{period} 曾明顯加碼"
    action = "買進" if event["direction"] > 0 else "賣出"
    return f"沉寂至少 5 個交易日後，{etf_text} 重新{action}"


def _prior_exit_dates(
    exit_history: dict[str, list[str]],
    event_date: str,
    new_etfs: list[str],
) -> dict[str, str]:
    exits: dict[str, str] = {}
    for etf in new_etfs:
        for date in reversed(exit_history.get(etf, [])):
            if date < event_date:
                exits[etf] = date
                break
    return exits


def _conviction_metrics(
    candidates: list[dict | None],
    end_index: int,
) -> dict:
    """Describe the rolling ten-session hold state at one point in time."""
    start = max(0, end_index + 1 - CONVICTION_SESSIONS)
    recent = candidates[start : end_index + 1]
    buy_positions = [
        index
        for index, item in enumerate(recent)
        if item and item["direction"] > 0
    ]
    buy_days = len(buy_positions)
    sell_days = sum(
        bool(item and item["direction"] < 0) for item in recent
    )
    net = round(sum(float(item["score"]) for item in recent if item), 4)
    participating = sorted(
        {
            etf
            for item in recent
            if item and item["direction"] > 0
            for etf in item["etfs"]
        }
    )
    breadth = len(participating)
    qualified = bool(
        buy_days >= MIN_CONVICTION_BUY_DAYS
        and buy_days >= sell_days + 2
        and net >= MIN_CONVICTION_NET
        and breadth >= MIN_CONVICTION_ETFS
    )
    return {
        "start_index": start,
        "recent": recent,
        "buy_days": buy_days,
        "sell_days": sell_days,
        "net": net,
        "participating": participating,
        "breadth": breadth,
        "qualified": qualified,
        "earliest_buy_expires_in": (
            buy_positions[0] + 1 if buy_positions else None
        ),
        "days_needed": max(0, MIN_CONVICTION_BUY_DAYS - buy_days),
        "balance_needed": max(0, sell_days + 2 - buy_days),
        "breadth_needed": max(0, MIN_CONVICTION_ETFS - breadth),
        "net_needed": round(max(0.0, MIN_CONVICTION_NET - net), 4),
    }


def _quiet_sessions_to_downgrade(metrics: dict) -> int | None:
    """How many new no-action sessions the current hold evidence can survive."""
    recent = list(metrics.get("recent") or [])
    if not metrics.get("qualified"):
        return 0
    for steps in range(1, CONVICTION_SESSIONS + 1):
        simulated = (recent + [None] * steps)[-CONVICTION_SESSIONS:]
        buy_days = sum(bool(item and item["direction"] > 0) for item in simulated)
        sell_days = sum(bool(item and item["direction"] < 0) for item in simulated)
        net = sum(float(item["score"]) for item in simulated if item)
        breadth = len(
            {
                etf
                for item in simulated
                if item and item["direction"] > 0
                for etf in item["etfs"]
            }
        )
        if not (
            buy_days >= MIN_CONVICTION_BUY_DAYS
            and buy_days >= sell_days + 2
            and net >= MIN_CONVICTION_NET
            and breadth >= MIN_CONVICTION_ETFS
        ):
            return steps
    return None


def _progress_label(metrics: dict) -> str:
    if metrics.get("qualified"):
        remaining = _quiet_sessions_to_downgrade(metrics)
        if remaining is None:
            return "續抱條件穩定"
        return f"若無新買，{remaining} 個交易日後轉為降溫"
    missing = []
    needed = max(
        int(metrics.get("days_needed") or 0),
        int(metrics.get("balance_needed") or 0),
    )
    if needed:
        missing.append(f"再 {needed} 個顯著買進日")
    breadth_needed = int(metrics.get("breadth_needed") or 0)
    if breadth_needed:
        missing.append(f"再 {breadth_needed} 檔 ETF 參與")
    net_needed = float(metrics.get("net_needed") or 0.0)
    if net_needed:
        missing.append(f"10日淨買再 +{net_needed:.2f}%")
    return (
        f"恢復強勢續抱尚缺：{'；'.join(missing)}"
        if missing
        else "等待下一次顯著加碼"
    )


def _latest_nonempty(
    candidates: list[dict | None],
    end_index: int,
    *,
    positive_only: bool = False,
) -> dict | None:
    for item in reversed(candidates[: end_index + 1]):
        if item and (not positive_only or item["direction"] > 0):
            return item
    return None


def _conviction_event(
    stock_id: str,
    candidates: list[dict | None],
    dates: list[str],
    selected_etfs: list[str],
) -> dict | None:
    """Return active, near-upgrade, or just-downgraded hold lifecycle state."""
    index = len(dates) - 1
    current_metrics = _conviction_metrics(candidates, index)
    previous_metrics = _conviction_metrics(candidates, index - 1)
    before_previous_metrics = _conviction_metrics(candidates, index - 2)
    current = candidates[index]
    latest_buy = _latest_nonempty(candidates, index, positive_only=True)
    base = current or latest_buy
    if not base:
        return None

    active = bool(current_metrics["qualified"])
    downgraded = bool(previous_metrics["qualified"] and not active)
    near_upgrade = bool(
        not active
        and not downgraded
        and current_metrics["breadth"] >= MIN_CONVICTION_ETFS
        and current_metrics["net"] >= MIN_CONVICTION_NET
        and max(
            current_metrics["days_needed"],
            current_metrics["balance_needed"],
        ) == 1
        and not (current and current["direction"] < 0)
    )
    if not (active or downgraded or near_upgrade):
        return None

    latest_direction = int(current["direction"]) if current else 0
    if active:
        event_type = "conviction_buy"
        if not previous_metrics["qualified"]:
            event_label = "升級續抱"
            lifecycle_label = "今日升級為續抱"
        elif (
            not before_previous_metrics["qualified"]
            and latest_direction > 0
        ):
            event_label = "持續加碼"
            lifecycle_label = "昨日升級・今日續買"
        elif latest_direction > 0:
            event_label = "持續加碼"
            lifecycle_label = "今日續買・續抱有效"
        elif latest_direction < 0:
            event_label = "續抱轉弱"
            lifecycle_label = "今日減碼・續抱警戒"
        else:
            event_label = "續抱有效"
            lifecycle_label = "今日未動・續抱仍有效"
    elif downgraded:
        event_type = "conviction_downgrade"
        event_label = "續抱降溫"
        lifecycle_label = (
            "加碼動能降溫・出現顯著減碼"
            if latest_direction < 0
            else "加碼動能降溫・尚無顯著賣出"
        )
    else:
        event_type = "conviction_watch"
        event_label = "接近續抱"
        lifecycle_label = "尚未升級・接近門檻"

    participating = list(current_metrics["participating"])
    etf_text = "、".join(ETF_LABEL.get(etf, etf) for etf in participating)
    latest_action_label = (
        "今日仍顯著加碼"
        if latest_direction > 0
        else "今日出現顯著減碼"
        if latest_direction < 0
        else (
            "尚無顯著賣出"
            if downgraded and current_metrics["sell_days"] == 0
            else "今日沒有新的顯著動作"
        )
    )
    progress = _progress_label(current_metrics)
    evidence_start = int(current_metrics["start_index"])
    buy_evidence_dates = [
        dates[evidence_start + offset]
        for offset, item in enumerate(current_metrics["recent"])
        if item and item["direction"] > 0
    ]
    sell_evidence_dates = [
        dates[evidence_start + offset]
        for offset, item in enumerate(current_metrics["recent"])
        if item and item["direction"] < 0
    ]
    return {
        **base,
        "stock_id": stock_id,
        "event_type": event_type,
        "event_label": event_label,
        "event_date": dates[-1],
        "current_confirmation_date": dates[-1],
        "age_sessions": 0,
        "score": current_metrics["net"],
        "buy_days": current_metrics["buy_days"],
        "sell_days": current_metrics["sell_days"],
        "etfs": participating,
        "breadth": len(participating),
        "reason": (
            f"近 {CONVICTION_SESSIONS} 日有 "
            f"{current_metrics['buy_days']} 日顯著買、"
            f"{current_metrics['sell_days']} 日顯著賣"
        ),
        "confirmation_label": f"{etf_text} 都曾參與",
        "confirmation": "conviction",
        "lifecycle_label": lifecycle_label,
        "latest_action_label": latest_action_label,
        "progress_label": progress,
        "evidence_expires_in": current_metrics["earliest_buy_expires_in"],
        "buy_evidence_dates": buy_evidence_dates,
        "sell_evidence_dates": sell_evidence_dates,
        "latest_buy_evidence_date": (
            buy_evidence_dates[-1] if buy_evidence_dates else None
        ),
        "quiet_sessions_to_downgrade": (
            _quiet_sessions_to_downgrade(current_metrics) if active else 0
        ),
        "conviction_qualified": active,
        "previous_conviction_qualified": bool(
            previous_metrics["qualified"]
        ),
    }


def _enrich_fresh_lifecycle(
    event: dict,
    candidates: list[dict | None],
    dates: list[str],
) -> None:
    """Add trigger/continuation and rolling-hold progress to a fresh event."""
    latest = candidates[-1] if candidates else None
    same_direction_today = bool(
        latest
        and latest["direction"] == event["direction"]
        and event["event_date"] != dates[-1]
    )
    action = "續買" if event["direction"] > 0 else "續賣"
    if same_direction_today:
        event["lifecycle_label"] = f"昨日觸發・今日{action}"
        event["current_confirmation_date"] = dates[-1]
        event["continuation_etfs"] = list(latest.get("etfs") or [])
        event["current_threshold_details"] = list(
            latest.get("threshold_details") or []
        )
        event["age_display"] = "current"
    elif event["event_date"] == dates[-1]:
        event["lifecycle_label"] = "今日觸發"
        event["current_confirmation_date"] = dates[-1]
        event["age_display"] = "current"
    else:
        event["lifecycle_label"] = "昨日觸發"
        event["age_display"] = "prior"

    current_metrics = _conviction_metrics(candidates, len(dates) - 1)
    previous_metrics = _conviction_metrics(candidates, len(dates) - 2)
    event["buy_days"] = current_metrics["buy_days"]
    event["sell_days"] = current_metrics["sell_days"]
    event["conviction_qualified"] = bool(current_metrics["qualified"])
    event["previous_conviction_qualified"] = bool(
        previous_metrics["qualified"]
    )
    event["progress_label"] = (
        "已達續抱條件；觸發退場後轉續抱"
        if current_metrics["qualified"] and event["direction"] > 0
        else _progress_label(current_metrics)
    )
    event["evidence_expires_in"] = current_metrics[
        "earliest_buy_expires_in"
    ]
    if (
        event["direction"] < 0
        and previous_metrics["qualified"]
    ):
        event["lifecycle_label"] = "續抱→賣出警示"
    elif event["direction"] < 0 and event["event_date"] == dates[-1]:
        event["lifecycle_label"] = "無訊號→賣出警示"


def build_event_snapshot(
    data: dict,
    etfs: list[str] | None = None,
    *,
    max_per_side: int | None = None,
    as_of: str | None = None,
) -> dict:
    """Return the stock action lifecycle board at the requested common session."""
    selected_etfs = list(etfs or data.get("etfs", []))
    dates = _shared_dates(data, selected_etfs)
    if as_of:
        dates = [date for date in dates if date <= as_of]
    if len(dates) < 5:
        raise ValueError("need at least 5 common ETF sessions for action events")
    date_set = set(dates)
    records: dict[str, dict[str, dict[str, dict]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    exit_history: dict[str, dict[str, list[str]]] = defaultdict(
        lambda: defaultdict(list)
    )

    for observation in data.get("observations", []):
        etf = str(observation.get("etf") or "")
        date = str(observation.get("date") or "")
        if etf not in selected_etfs:
            continue
        fallback_threshold = _move_threshold(observation)
        for move in observation.get("stocks", []):
            category = str(move.get("category") or "未分類")
            if category == "未分類":
                continue
            stock_id = str(move.get("id") or "")
            if move.get("position_event") == "full_exit":
                exit_history[stock_id][etf].append(date)
            if date not in date_set:
                continue
            records[stock_id][date][etf] = {
                **move,
                "id": stock_id,
                "category": category,
                "fallback_threshold": fallback_threshold,
            }

    _assign_stock_thresholds(records, dates)

    events: list[dict] = []
    series: dict[str, tuple[list[dict | None], list[dict | None]]] = {}
    first_fresh = max(0, len(dates) - FRESH_SESSIONS)
    for stock_id, stock_dates in records.items():
        candidates = [
            _candidate(stock_dates, dates, index, len(selected_etfs))
            for index in range(len(dates))
        ]
        confirmations = [
            _confirmation(candidates, index) for index in range(len(dates))
        ]
        series[stock_id] = (candidates, confirmations)
        for index in range(first_fresh, len(dates)):
            confirmed = confirmations[index]
            if not confirmed:
                continue
            previous_confirmed = confirmations[index - 1] if index > 0 else None
            if (
                previous_confirmed
                and previous_confirmed["direction"] == confirmed["direction"]
                and not confirmed["structural"]
            ):
                # This is continuation after the entry moment, which fresh lanes are
                # explicitly designed not to surface.
                continue
            prior_net, buy_days, sell_days, idle = _prior_context(
                candidates, confirmed["start_index"]
            )
            confirmed = {
                **confirmed,
                "reentry_exit_dates": _prior_exit_dates(
                    exit_history.get(stock_id, {}),
                    dates[index],
                    confirmed["new_etfs"],
                ),
                "prior_opposite_range": _prior_opposite_range(
                    candidates,
                    dates,
                    confirmed["start_index"],
                    confirmed["direction"],
                ),
            }
            event_type = _event_type(
                confirmed, prior_net, buy_days, sell_days, idle
            )
            if not event_type:
                continue
            labels = BUY_EVENT_LABELS if confirmed["direction"] > 0 else SELL_EVENT_LABELS
            event = {
                **confirmed,
                "stock_id": stock_id,
                "event_type": event_type,
                "event_label": labels[event_type],
                "event_date": dates[index],
                "age_sessions": len(dates) - 1 - index,
                "prior_net": prior_net,
                "prior_buy_days": buy_days,
                "prior_sell_days": sell_days,
            }
            event["confirmation_label"] = _confirmation_label(
                event, len(selected_etfs)
            )
            event["reason"] = _reason(event)
            _enrich_fresh_lifecycle(event, candidates, dates)
            metadata = _display_metadata(event, len(selected_etfs))
            if not metadata:
                continue
            event.update(metadata)
            events.append(event)

    priority = {
        "new_position": 0,
        "reentry_position": 0,
        "sell_to_buy": 0,
        "full_exit": 0,
        "buy_to_sell": 0,
        "trial_position": 1,
        "restart_buy": 2,
        "restart_sell": 2,
    }
    events.sort(
        key=lambda event: (
            event["age_sessions"],
            priority[event["event_type"]],
            -event["breadth"],
            -abs(float(event["score"])),
        )
    )
    # If a stock generated two fresh events, only the latest action is useful.
    deduped: list[dict] = []
    seen: set[str] = set()
    for event in events:
        if event["stock_id"] in seen:
            continue
        seen.add(event["stock_id"])
        deduped.append(event)

    buying = [event for event in deduped if event["direction"] > 0]
    selling = [event for event in deduped if event["direction"] < 0]
    if max_per_side is not None:
        buying = buying[:max_per_side]
        selling = selling[:max_per_side]

    buying_by_stock = {event["stock_id"]: event for event in buying}
    selling_by_stock = {event["stock_id"]: event for event in selling}
    holding = []
    for stock_id, (candidates, _confirmations) in series.items():
        event = _conviction_event(
            stock_id, candidates, dates, selected_etfs
        )
        if not event:
            continue
        if stock_id in buying_by_stock:
            # A fresh entry/re-entry/reversal is the more actionable state.
            # Its card already carries the rolling hold gap or confirms that
            # the hold threshold has also been reached.
            continue
        if (
            event["event_type"] == "conviction_downgrade"
            and stock_id in selling_by_stock
        ):
            selling_by_stock[stock_id]["lifecycle_label"] = "續抱→賣出警示"
            continue
        if metadata := _display_metadata(event, len(selected_etfs)):
            event.update(metadata)
            holding.append(event)
    holding.sort(
        key=lambda event: (
            {
                "conviction_buy": 0,
                "conviction_watch": 1,
                "conviction_downgrade": 2,
            }.get(str(event.get("event_type") or ""), 3),
            int(event.get("quiet_sessions_to_downgrade") or 99),
            -float(event["score"]),
            -int(event["buy_days"]),
            -int(event["breadth"]),
        )
    )
    for event in [*buying, *holding, *selling]:
        event["flow_trend_20d"] = _flow_trend(
            records.get(str(event.get("stock_id") or ""), {}), dates
        )
    return {
        "as_of": dates[-1],
        "dates": dates,
        "etfs": selected_etfs,
        "buying": buying,
        "holding": holding,
        "selling": selling,
        "methodology": {
            "fresh_sessions": FRESH_SESSIONS,
            "context_sessions": CONTEXT_SESSIONS,
            "idle_sessions": IDLE_SESSIONS,
            "conviction_sessions": CONVICTION_SESSIONS,
            "conviction_min_buy_days": MIN_CONVICTION_BUY_DAYS,
            "conviction_min_net": MIN_CONVICTION_NET,
            "conviction_min_participating_etfs": MIN_CONVICTION_ETFS,
            "min_normalized_flow": MIN_NORMALIZED_FLOW,
            "median_fraction": MEDIAN_FRACTION,
            "stock_median_fraction": STOCK_MEDIAN_FRACTION,
            "stock_baseline_sessions": CONVICTION_SESSIONS,
            "stock_baseline_min_samples": MIN_STOCK_BASELINE_SAMPLES,
            "significance_scope": "same ETF + same stock + same direction",
            "ordinary_qualification": "at least 2 ETFs aligned",
            "single_etf_exceptions": (
                "position-list change OR reversal confirmed for 2 sessions"
            ),
            "single_etf_ordinary_actions_hidden": True,
            "hold_min_participating_etfs": 2,
            "same_direction_continuations_labelled": True,
            "strong_continuations_shown_as_hold_evidence": True,
            "hold_survives_quiet_session_while_window_qualifies": True,
            "hold_downgrade_transition_shown": True,
            "concepts_interpreted": False,
        },
    }
