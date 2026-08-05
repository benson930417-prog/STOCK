"""Special 00981A-only follow-the-manager signal for the backtest panel."""
from __future__ import annotations

from src.etf_intent_v3 import flow_adjusted_moves


def build_981_follow_signal(history: dict, tags: dict | None = None) -> dict:
    """Hold only while 00981A reports a copyable active share increase.

    A buy day requires both actual shares and the unit-scale-adjusted active
    allocation residual to increase. The next disclosure without such an
    increase ends the state. The portfolio engine executes both transitions at
    the following tradable open, so disclosure-day prices are never used.
    """
    tags = tags or {}
    source_dates = sorted(history)
    dates = source_dates[1:]
    moves_by_date: dict[str, dict[str, dict]] = {}
    names: dict[str, str] = {}
    universe: set[str] = set()
    for previous_date, current_date in zip(source_dates, source_dates[1:]):
        rows = flow_adjusted_moves(
            history[current_date],
            history[previous_date],
            etf="00981A",
            date=current_date,
            tags=tags,
        )
        moves_by_date[current_date] = {str(row["id"]): row for row in rows}
        for row in rows:
            code = str(row["id"])
            universe.add(code)
            names[code] = str(row.get("name") or code)

    state_history: dict[str, list[dict]] = {code: [] for code in sorted(universe)}
    boards: dict[str, dict] = {}
    previous_state = {code: "none" for code in universe}
    for day in dates:
        buying: list[dict] = []
        for code in sorted(universe):
            move = (moves_by_date.get(day) or {}).get(code) or {}
            active_flow = float(move.get("active_flow") or 0.0)
            raw_delta = int(move.get("raw_delta_shares") or 0)
            is_buying = bool(move.get("copyable")) and raw_delta > 0 and active_flow > 0
            state = "buy" if is_buying else "none"
            prior = previous_state[code]
            if state == "buy" and prior != "buy":
                transition = "00981A 開始主動加碼"
            elif state == "buy":
                transition = "00981A 持續主動加碼"
            elif prior == "buy":
                transition = "00981A 本期未續買"
            else:
                transition = "無 00981A 續買"
            state_history[code].append(
                {
                    "date": day,
                    "state": state,
                    "direction": 1 if is_buying else 0,
                    "score": round(active_flow, 6) if is_buying else 0.0,
                    "participants": ["00981A"] if is_buying else [],
                    "transition": transition,
                    "raw_delta_shares": raw_delta,
                    "active_flow": round(active_flow, 6),
                    "unit_quality": str(move.get("unit_quality") or ""),
                }
            )
            previous_state[code] = state
            if is_buying:
                buying.append(
                    {
                        "stock_id": code,
                        "name": names.get(code, code),
                        "state": "buy",
                        "score": round(active_flow, 6),
                        "decision_tier": "981-follow",
                        "raw_delta_shares": raw_delta,
                        "active_flow": round(active_flow, 6),
                    }
                )
        buying.sort(key=lambda row: float(row["active_flow"]), reverse=True)
        boards[day] = {"watching": [], "buying": buying, "selling": []}

    return {
        "schema_version": 1,
        "strategy": "00981A-follow-buying",
        "as_of": dates[-1] if dates else None,
        "dates": dates,
        "state_history": state_history,
        "boards": boards,
        "methodology": {
            "entry_signal": "00981A copyable raw-share and flow-adjusted active-allocation increase",
            "exit_signal": "next 00981A disclosure without a copyable increase",
            "execution": "next tradable session open",
            "threshold": "any positive copyable increase; no consensus or significance gate",
        },
    }
