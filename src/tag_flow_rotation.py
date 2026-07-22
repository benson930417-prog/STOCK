"""Window-independent rotation states for active-ETF category flow.

The dashboard may display 1/10/20/... sessions, but that viewport must never
decide the headline.  This module uses all common history and three smooth
memories instead:

* fast pressure: EWMA half-life 3 sessions;
* underlying direction: EWMA half-life 10 sessions;
* background position: EWMA half-life 20 sessions.

Magnitude is judged against the category's own prior fast-pressure history,
direction needs ETF breadth, and a state change needs two consecutive sessions.
Only the stock's single ``category`` is interpreted; concepts are never read.
"""
from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
import math


FAST_HALF_LIFE = 3.0
TREND_HALF_LIFE = 10.0
BACKGROUND_HALF_LIFE = 20.0
CONFIRM_SESSIONS = 2
MIN_PERCENTILE_HISTORY = 10
ABSOLUTE_NOISE_FLOOR = 0.004
DEFAULT_CHART_DAYS = 10
CURRENT_PRESSURE_ALERT_RATIO = 0.5

PHASES = {
    "buy_entering": {
        "label": "買盤剛進場",
        "short": "剛進場",
        "group": "buy",
        "priority": 3,
    },
    "buy_accelerating": {
        "label": "買盤加速",
        "short": "加速",
        "group": "buy",
        "priority": 1,
    },
    "buy_continuing": {
        "label": "加碼延續",
        "short": "延續加碼",
        "group": "buy",
        "priority": 2,
    },
    "buy_cooling": {
        "label": "買盤退潮，背景仍偏買",
        "short": "買盤退潮",
        "group": "transition",
        "priority": 4,
    },
    "recent_selling": {
        "label": "近期轉為減碼",
        "short": "近期轉賣",
        "group": "sell",
        "priority": 5,
    },
    "sell_continuing": {
        "label": "減碼延續",
        "short": "持續減碼",
        "group": "sell",
        "priority": 6,
    },
    "sell_easing": {
        "label": "賣壓緩和，背景仍偏賣",
        "short": "賣壓緩和",
        "group": "transition",
        "priority": 7,
    },
    "no_consensus": {
        "label": "方向不一致／證據不足",
        "short": "證據不足",
        "group": "neutral",
        "priority": 8,
    },
}

BUY_PHASES = {"buy_entering", "buy_accelerating", "buy_continuing"}
SELL_PHASES = {"recent_selling", "sell_continuing"}
TRANSITION_PHASES = {"buy_cooling", "sell_easing"}


def shared_dates(data: dict, etfs: list[str]) -> list[str]:
    by_etf = data.get("dates", {}).get("by_etf", {})
    sets = [set(by_etf.get(etf, [])) for etf in etfs]
    if not sets or any(not values for values in sets):
        return []
    return sorted(set.intersection(*sets))


def ewma_series(values: list[float], half_life: float) -> list[float]:
    if not values:
        return []
    alpha = 1.0 - math.exp(math.log(0.5) / half_life)
    result = [float(values[0])]
    for value in values[1:]:
        result.append(alpha * float(value) + (1.0 - alpha) * result[-1])
    return result


def _activity_threshold(values: list[float]) -> float:
    if not values:
        return ABSOLUTE_NOISE_FLOOR
    mean_absolute = sum(abs(value) for value in values) / len(values)
    return max(ABSOLUTE_NOISE_FLOOR, mean_absolute * 0.25)


def _direction(value: float, threshold: float) -> int:
    if value > threshold:
        return 1
    if value < -threshold:
        return -1
    return 0


def _empirical_percentile(value: float, history: list[float]) -> float | None:
    if len(history) < MIN_PERCENTILE_HISTORY:
        return None
    ordered = sorted(abs(item) for item in history)
    return round(100.0 * bisect_right(ordered, abs(value)) / len(ordered), 1)


def strength_band(percentile: float | None) -> str:
    """Turn an audit percentile into a plain-language user-facing band."""
    if percentile is None:
        return "歷史樣本累積中"
    if percentile >= 80:
        return "自身力道強"
    if percentile >= 50:
        return "自身力道中"
    return "自身力道一般"


def _candidate_phase(
    fast: float,
    trend: float,
    background: float,
    threshold: float,
    buyers: int,
    sellers: int,
    required_breadth: int,
) -> str:
    fast_direction = _direction(fast, threshold)
    trend_direction = _direction(trend, threshold * 0.65)
    background_direction = _direction(background, threshold * 0.50)

    if fast_direction > 0 and buyers >= required_breadth:
        if trend_direction > 0:
            return "buy_accelerating" if fast - trend > threshold * 0.50 else "buy_continuing"
        if background_direction < 0:
            return "sell_easing"
        return "buy_entering"

    if fast_direction < 0 and sellers >= required_breadth:
        if trend_direction < 0:
            return "sell_continuing"
        return "recent_selling"

    # Cooling/easing is allowed without breadth because it describes the loss
    # of an old impulse, not a new coordinated buy/sell claim.
    if trend_direction > 0:
        return "buy_cooling"
    if trend_direction < 0:
        return "sell_easing"
    return "no_consensus"


def _confirmed_phase_history(candidates: list[str]) -> tuple[list[str], int]:
    if not candidates:
        return [], 0
    stable = "no_consensus"
    stable_history: list[str] = []
    run_phase = ""
    run_length = 0
    for candidate in candidates:
        if candidate == run_phase:
            run_length += 1
        else:
            run_phase = candidate
            run_length = 1
        if run_length >= CONFIRM_SESSIONS:
            stable = candidate
        stable_history.append(stable)
    return stable_history, run_length


def _stock_pressure_pools(
    stocks: dict,
    dates: list[str],
    etfs: list[str],
    limit: int,
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """Rank stock contributors and preserve the strict all-ETF pools."""
    buying_contributors: list[dict] = []
    selling_contributors: list[dict] = []
    consensus_buying: list[dict] = []
    consensus_selling: list[dict] = []
    for stock in stocks.values():
        raw_fast_by_etf: dict[str, float] = {}
        fast_by_etf: dict[str, float] = {}
        for etf in etfs:
            values = [stock["daily_by_etf"][etf].get(date, 0.0) for date in dates]
            fast = ewma_series(values, FAST_HALF_LIFE)[-1]
            threshold = _activity_threshold(values)
            raw_fast_by_etf[etf] = fast
            fast_by_etf[etf] = fast if abs(fast) > threshold else 0.0
        # EWMA is linear, so the raw per-stock average is the stock's actual
        # contribution to the category's 3-session current pressure.
        score = sum(raw_fast_by_etf.values()) / len(etfs)
        buyers = sum(value > 0 for value in fast_by_etf.values())
        sellers = sum(value < 0 for value in fast_by_etf.values())
        row = {
            "id": stock["id"],
            "name": stock["name"],
            "pressure": round(score, 4),
            "buyers": buyers,
            "sellers": sellers,
            "by_etf": {
                etf: round(value, 4) for etf, value in raw_fast_by_etf.items()
            },
        }
        if score > 0:
            buying_contributors.append(row)
        elif score < 0:
            selling_contributors.append(row)
        if buyers == len(etfs) and score > 0:
            consensus_buying.append(row)
        if sellers == len(etfs) and score < 0:
            consensus_selling.append(row)
    buying_contributors.sort(key=lambda row: -row["pressure"])
    selling_contributors.sort(key=lambda row: row["pressure"])
    consensus_buying.sort(key=lambda row: -row["pressure"])
    consensus_selling.sort(key=lambda row: row["pressure"])
    return (
        buying_contributors[:limit],
        selling_contributors[:limit],
        consensus_buying[:limit],
        consensus_selling[:limit],
    )


def _window_totals(values: list[float]) -> dict[str, float]:
    return {
        str(days): round(sum(values[-min(days, len(values)) :]), 4)
        for days in (10, 20)
    }


def build_rotation_snapshot(
    data: dict,
    etfs: list[str] | None = None,
    *,
    chart_days: int = DEFAULT_CHART_DAYS,
    stock_pool_limit: int = 5,
) -> dict:
    """Return the latest category rotation story using all common history."""
    selected_etfs = list(etfs or data.get("etfs", []))
    dates = shared_dates(data, selected_etfs)
    if len(dates) < 5:
        raise ValueError("need at least 5 common ETF sessions for rotation state")
    date_set = set(dates)
    categories: dict[str, dict] = {}

    for observation in data.get("observations", []):
        etf = observation.get("etf")
        date = observation.get("date")
        if etf not in selected_etfs or date not in date_set:
            continue
        for move in observation.get("stocks", []):
            category = str(move.get("category") or "未分類")
            if category == "未分類":
                continue
            flow = float(move.get("flow") or 0.0)
            sector = categories.setdefault(
                category,
                {
                    "category": category,
                    "daily_by_etf": defaultdict(lambda: defaultdict(float)),
                    "stocks": {},
                },
            )
            sector["daily_by_etf"][etf][date] += flow
            stock_id = str(move.get("id") or "")
            stock = sector["stocks"].setdefault(
                stock_id,
                {
                    "id": stock_id,
                    "name": str(move.get("name") or stock_id),
                    "daily_by_etf": defaultdict(lambda: defaultdict(float)),
                },
            )
            stock["daily_by_etf"][etf][date] += flow

    rows: list[dict] = []
    required_breadth = 1 if len(selected_etfs) == 1 else 2
    for sector in categories.values():
        daily_by_etf = {
            etf: [sector["daily_by_etf"][etf].get(date, 0.0) for date in dates]
            for etf in selected_etfs
        }
        daily = [
            sum(daily_by_etf[etf][index] for etf in selected_etfs) / len(selected_etfs)
            for index in range(len(dates))
        ]
        fast_series = ewma_series(daily, FAST_HALF_LIFE)
        trend_series = ewma_series(daily, TREND_HALF_LIFE)
        background_series = ewma_series(daily, BACKGROUND_HALF_LIFE)
        etf_fast_series = {
            etf: ewma_series(values, FAST_HALF_LIFE)
            for etf, values in daily_by_etf.items()
        }

        candidates: list[str] = []
        breadth_history: list[tuple[int, int]] = []
        threshold_history: list[float] = []
        for index in range(len(dates)):
            prefix = daily[: index + 1]
            threshold = _activity_threshold(prefix)
            threshold_history.append(threshold)
            buyers = 0
            sellers = 0
            for etf in selected_etfs:
                etf_threshold = _activity_threshold(daily_by_etf[etf][: index + 1])
                etf_direction = _direction(etf_fast_series[etf][index], etf_threshold)
                buyers += etf_direction > 0
                sellers += etf_direction < 0
            breadth_history.append((buyers, sellers))
            candidates.append(
                _candidate_phase(
                    fast_series[index],
                    trend_series[index],
                    background_series[index],
                    threshold,
                    buyers,
                    sellers,
                    required_breadth,
                )
            )

        stable_history, candidate_run = _confirmed_phase_history(candidates)
        stable_phase = stable_history[-1]
        candidate_phase = candidates[-1]
        pending_phase = candidate_phase if candidate_phase != stable_phase else None
        buyers, sellers = breadth_history[-1]
        history_for_percentile = fast_series[:-1]
        percentile = _empirical_percentile(fast_series[-1], history_for_percentile)
        threshold = threshold_history[-1]
        pressure_score = fast_series[-1] / threshold if threshold else 0.0
        state_age = 0
        for phase in reversed(stable_history):
            if phase != stable_phase:
                break
            state_age += 1
        confirmed = pending_phase is None and candidate_run >= CONFIRM_SESSIONS
        if confirmed and max(buyers, sellers) == len(selected_etfs) and len(dates) >= 20:
            confidence = "高"
        elif confirmed and max(buyers, sellers) >= required_breadth and len(dates) >= 10:
            confidence = "中"
        else:
            confidence = "低"

        top_buying, top_selling, buy_pool, sell_pool = _stock_pressure_pools(
            sector["stocks"], dates, selected_etfs, stock_pool_limit
        )
        chart_count = min(max(1, chart_days), len(dates))
        row = {
            "category": sector["category"],
            "phase": stable_phase,
            "phase_label": PHASES[stable_phase]["label"],
            "phase_short": PHASES[stable_phase]["short"],
            "phase_group": PHASES[stable_phase]["group"],
            "phase_priority": PHASES[stable_phase]["priority"],
            "candidate_phase": candidate_phase,
            "pending_phase": pending_phase,
            "pending_label": PHASES[pending_phase]["label"] if pending_phase else None,
            "state_age": state_age,
            "confidence": confidence,
            "fast": round(fast_series[-1], 4),
            "trend": round(trend_series[-1], 4),
            "background": round(background_series[-1], 4),
            "pressure_score": round(pressure_score, 3),
            "strength_percentile": percentile,
            "strength_label": strength_band(percentile),
            "buyers": buyers,
            "sellers": sellers,
            "etf_count": len(selected_etfs),
            "current_sell_alert": pressure_score <= -CURRENT_PRESSURE_ALERT_RATIO,
            "current_buy_alert": pressure_score >= CURRENT_PRESSURE_ALERT_RATIO,
            "window_totals": _window_totals(daily),
            "chart_dates": dates[-chart_count:],
            "daily": [round(value, 4) for value in daily[-chart_count:]],
            "fast_series": [round(value, 4) for value in fast_series[-chart_count:]],
            "trend_series": [round(value, 4) for value in trend_series[-chart_count:]],
            "top_buying_stocks": top_buying,
            "top_selling_stocks": top_selling,
            "stocks_all_three": buy_pool,
            "stocks_all_three_selling": sell_pool,
        }
        rows.append(row)

    rows.sort(key=lambda row: (row["phase_priority"], -abs(row["pressure_score"])))
    by_pressure = sorted(rows, key=lambda row: row["pressure_score"], reverse=True)
    total_rows = len(by_pressure)
    for rank, row in enumerate(by_pressure, 1):
        row["cross_section_rank"] = rank
        row["cross_section_total"] = total_rows

    return {
        "as_of": dates[-1],
        "dates": dates,
        "etfs": selected_etfs,
        "history_sessions": len(dates),
        "chart_days": min(max(1, chart_days), len(dates)),
        "methodology": {
            "fast_half_life": FAST_HALF_LIFE,
            "trend_half_life": TREND_HALF_LIFE,
            "background_half_life": BACKGROUND_HALF_LIFE,
            "confirmation_sessions": CONFIRM_SESSIONS,
            "strength_reference": "own prior fast-pressure history",
            "breadth_required": required_breadth,
            "current_pressure_alert_ratio": CURRENT_PRESSURE_ALERT_RATIO,
            "window_independent": True,
        },
        "rows": rows,
    }


def phase_explanation(row: dict) -> str:
    """Concise, explicit reason suitable for UI and LINE text."""
    magnitude = row.get("strength_label") or strength_band(row.get("strength_percentile"))
    if row.get("fast", 0.0) > 0:
        breadth = f"{row['buyers']}/{row['etf_count']} ETF 近期偏買"
    elif row.get("fast", 0.0) < 0:
        breadth = f"{row['sellers']}/{row['etf_count']} ETF 近期偏賣"
    else:
        breadth = "近期壓力接近中性"
    pending = f"；轉向待確認：{row['pending_label']}" if row.get("pending_label") else ""
    return f"{magnitude}｜{breadth}｜方向信心 {row['confidence']}{pending}"
