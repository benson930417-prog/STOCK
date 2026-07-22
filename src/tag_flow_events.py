"""Stock-level active-ETF action events for the V2 copy radar.

V1 answers which category currently has pressure.  V2 answers a narrower and
more actionable question: which disclosed stock action has *just changed*?
It separates fresh buy/sell actions from unusually persistent buying that can
support a hold decision.  It still excludes chart windows, cash ranking, and
category-level magnitude.  Category is context only; concepts are ignored.
"""
from __future__ import annotations

from collections import defaultdict


ETF_LABEL = {"00403A": "403", "00981A": "981", "00991A": "991"}
CONTEXT_SESSIONS = 20
IDLE_SESSIONS = 5
FRESH_SESSIONS = 2
MIN_NORMALIZED_FLOW = 0.02
MEDIAN_FRACTION = 0.35
MIN_CONTEXT_NET = 0.05
MIN_CONTEXT_ACTION_DAYS = 2
CONVICTION_SESSIONS = 10
MIN_CONVICTION_BUY_DAYS = 4
MIN_CONVICTION_NET = 0.20

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
        significant = abs(float(move.get("flow") or 0.0)) >= float(
            move.get("threshold") or MIN_NORMALIZED_FLOW
        )
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
        # Conflicting ETF actions are useful evidence for V1, but there is no
        # clean action to copy, so V2 deliberately stays silent.
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


def _conviction_event(
    stock_id: str,
    candidates: list[dict | None],
    confirmations: list[dict | None],
    dates: list[str],
    selected_etfs: list[str],
) -> dict | None:
    """Return strong continuing buying as hold evidence, never as a new entry."""
    current = confirmations[-1] if confirmations else None
    if not current or current["direction"] <= 0:
        return None
    recent = candidates[-CONVICTION_SESSIONS:]
    buy_days = sum(bool(item and item["direction"] > 0) for item in recent)
    sell_days = sum(bool(item and item["direction"] < 0) for item in recent)
    net = round(sum(float(item["score"]) for item in recent if item), 4)
    if (
        buy_days < MIN_CONVICTION_BUY_DAYS
        or buy_days < sell_days + 2
        or net < MIN_CONVICTION_NET
    ):
        return None
    participating = [
        etf
        for etf in selected_etfs
        if any(
            item and item["direction"] > 0 and etf in item["etfs"]
            for item in recent
        )
    ]
    etf_text = "、".join(ETF_LABEL.get(etf, etf) for etf in participating)
    return {
        **current,
        "stock_id": stock_id,
        "event_type": "conviction_buy",
        "event_label": "持續加碼",
        "event_date": dates[-1],
        "age_sessions": 0,
        "score": net,
        "buy_days": buy_days,
        "sell_days": sell_days,
        "etfs": participating,
        "breadth": len(participating),
        "reason": (
            f"近 {CONVICTION_SESSIONS} 日有 {buy_days} 日明顯加碼、"
            f"{sell_days} 日明顯減碼；最近仍在買"
        ),
        "confirmation_label": f"{etf_text} 都曾參與",
    }


def build_event_snapshot(
    data: dict,
    etfs: list[str] | None = None,
    *,
    max_per_side: int = 6,
) -> dict:
    """Return only fresh, confirmed, non-continuation stock action events."""
    selected_etfs = list(etfs or data.get("etfs", []))
    dates = _shared_dates(data, selected_etfs)
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
        threshold = _move_threshold(observation)
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
                "threshold": threshold,
            }

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
                # This is continuation after the entry moment, which V2 is
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

    buying = [event for event in deduped if event["direction"] > 0][
        :max_per_side
    ]
    selling = [event for event in deduped if event["direction"] < 0][
        :max_per_side
    ]
    holding = []
    for stock_id, (candidates, confirmations) in series.items():
        if stock_id in seen:
            continue
        event = _conviction_event(
            stock_id, candidates, confirmations, dates, selected_etfs
        )
        if event:
            holding.append(event)
    holding.sort(
        key=lambda event: (
            -float(event["score"]),
            -int(event["buy_days"]),
            -int(event["breadth"]),
        )
    )
    return {
        "as_of": dates[-1],
        "dates": dates,
        "etfs": selected_etfs,
        "buying": buying,
        "holding": holding[:4],
        "selling": selling,
        "methodology": {
            "fresh_sessions": FRESH_SESSIONS,
            "context_sessions": CONTEXT_SESSIONS,
            "idle_sessions": IDLE_SESSIONS,
            "conviction_sessions": CONVICTION_SESSIONS,
            "conviction_min_buy_days": MIN_CONVICTION_BUY_DAYS,
            "conviction_min_net": MIN_CONVICTION_NET,
            "min_normalized_flow": MIN_NORMALIZED_FLOW,
            "median_fraction": MEDIAN_FRACTION,
            "confirmation": "position-list change OR 2 ETFs same day OR 2 consecutive sessions",
            "continuations_excluded_from_fresh_signals": True,
            "strong_continuations_shown_as_hold_evidence": True,
            "concepts_interpreted": False,
        },
    }
