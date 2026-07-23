"""Final active-ETF consensus state engine.

V4 separates *state* from *score*:

* yellow/watch = one manager produced a high-information precursor;
* red/buy = at least two managers independently confirmed buying;
* green/sell = at least two managers independently confirmed selling.

The colour is a hard gate.  A large one-manager score can never become a
red/green conclusion.  The score only ranks maturity inside the same lane.
All thresholds are assigned from preceding observations by the V3 primitives;
concept tags are never interpreted.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import math

from src.etf_intent_v3 import (
    ETF_LABEL,
    _common_dates,
    _display_name,
    assign_no_lookahead_thresholds,
    build_move_store,
)


HISTORY_SESSIONS = 260
CHART_SESSIONS = 20
SIGNAL_OVERLAP_SESSIONS = 3
SUPPORT_SESSIONS = 10
WATCH_SESSIONS = 3
MAINTENANCE_SCORE = 40
EWMA_HALFLIVES = (3, 10, 20)
STRUCTURAL_EVENTS = {"new_position", "full_exit"}


def _alpha(half_life: int) -> float:
    return 1.0 - math.exp(math.log(0.5) / half_life)


def _direction(value: float, epsilon: float = 1e-9) -> int:
    if value > epsilon:
        return 1
    if value < -epsilon:
        return -1
    return 0


def _clipped_ratio(move: dict | None) -> float:
    if not move or not move.get("copyable"):
        return 0.0
    threshold = float(move.get("threshold") or 0.0)
    flow = float(move.get("active_flow") or 0.0)
    if threshold <= 0:
        return 0.0
    return max(-3.0, min(3.0, flow / threshold))


def _score(
    features: dict[str, dict],
    participants: list[str],
    direction: int,
) -> tuple[int, dict[str, int]]:
    """Score confirmed consensus; never decides whether consensus exists."""
    breadth = len(participants)
    breadth_points = 35 if breadth >= 3 else 25 if breadth >= 2 else 0
    counts = sorted(
        (
            int(features[etf]["buy_days_10" if direction > 0 else "sell_days_10"])
            for etf in participants
        ),
        reverse=True,
    )
    weaker_count = counts[1] if len(counts) >= 2 else 0
    persistence = round(30 * min(weaker_count, 5) / 5)
    strengths = sorted(
        (
            float(
                features[etf][
                    "buy_strength_10" if direction > 0 else "sell_strength_10"
                ]
            )
            for etf in participants
        ),
        reverse=True,
    )
    weaker_strength = strengths[1] if len(strengths) >= 2 else 0.0
    strength = round(25 * min(weaker_strength / 6.0, 1.0))
    top_two = sorted(
        participants,
        key=lambda etf: float(
            features[etf][
                "buy_strength_10" if direction > 0 else "sell_strength_10"
            ]
        ),
        reverse=True,
    )[:2]
    short_aligned = all(
        float(features[etf]["ewma_3"]) * direction > 0 for etf in top_two
    )
    background_aligned = all(
        float(features[etf]["ewma_10"]) * direction > 0
        and float(features[etf]["ewma_20"]) * direction >= 0
        for etf in top_two
    )
    alignment = (5 if short_aligned else 0) + (
        5 if background_aligned else 0
    )
    components = {
        "independent_etfs": breadth_points,
        "joint_persistence": persistence,
        "relative_strength": strength,
        "horizon_alignment": alignment,
    }
    return min(100, sum(components.values())), components


def _watch_score(
    feature: dict,
    *,
    other_same_direction_ratio: float,
    watch_kind: str,
) -> tuple[int, dict[str, int]]:
    event_points = {
        "reentry": 40,
        "full_exit": 40,
        "reversal": 38,
        "new_position": 35,
        "restart": 28,
        "consensus_cooling": 30,
    }.get(watch_kind, 25)
    magnitude = min(
        25,
        round(25 * min(float(feature["signal_significance_ratio"]) / 3, 1)),
    )
    repeat = min(20, int(feature["same_days_3"]) * 7)
    latent = min(15, round(15 * min(other_same_direction_ratio, 1)))
    components = {
        "event_quality": event_points,
        "relative_size": magnitude,
        "repeat_action": repeat,
        "latent_second_etf": latent,
    }
    return min(100, sum(components.values())), components


def _features_for_date(
    *,
    stock_id: str,
    date_index: int,
    common_dates: list[str],
    etfs: list[str],
    records: dict,
    histories: dict[str, list[dict]],
    ewmas: dict[str, dict[int, float]],
) -> dict[str, dict]:
    date = common_dates[date_index]
    result = {}
    for etf in etfs:
        move = records.get(date, {}).get(stock_id, {}).get(etf)
        ratio = _clipped_ratio(move)
        for half_life in EWMA_HALFLIVES:
            alpha = _alpha(half_life)
            ewmas[etf][half_life] = (
                alpha * ratio + (1.0 - alpha) * ewmas[etf][half_life]
            )
        row = {
            "date": date,
            "ratio": round(ratio, 4),
            "active_flow": round(float((move or {}).get("active_flow") or 0.0), 6),
            "threshold": round(float((move or {}).get("threshold") or 0.0), 6),
            "significance_ratio": round(
                float((move or {}).get("significance_ratio") or 0.0), 2
            ),
            "significant": bool((move or {}).get("significant")),
            "copyable": bool((move or {}).get("copyable")),
            "position_event": str((move or {}).get("position_event") or ""),
            "raw_delta_shares": int((move or {}).get("raw_delta_shares") or 0),
            "money_twd": float((move or {}).get("money_twd") or 0.0),
            "unit_quality": str((move or {}).get("unit_quality") or ""),
        }
        histories[etf].append(row)
        trailing_10 = histories[etf][-SUPPORT_SESSIONS:]
        trailing_3 = histories[etf][-SIGNAL_OVERLAP_SESSIONS:]
        significant_rows = [
            item
            for item in trailing_10
            if item["significant"] and _direction(float(item["ratio"]))
        ]
        prior_significant_rows = [
            item
            for item in trailing_10[:-1]
            if item["significant"] and _direction(float(item["ratio"]))
        ]
        last_significant = significant_rows[-1] if significant_rows else None
        prior_significant = (
            prior_significant_rows[-1] if prior_significant_rows else None
        )
        last_age = (
            date_index
            - common_dates.index(str(last_significant["date"]))
            if last_significant
            else None
        )
        last_direction = (
            _direction(float(last_significant["ratio"]))
            if last_significant
            else 0
        )
        result[etf] = {
            **row,
            "ewma_3": round(ewmas[etf][3], 4),
            "ewma_10": round(ewmas[etf][10], 4),
            "ewma_20": round(ewmas[etf][20], 4),
            "buy_days_10": sum(
                item["significant"] and float(item["ratio"]) > 0
                for item in trailing_10
            ),
            "sell_days_10": sum(
                item["significant"] and float(item["ratio"]) < 0
                for item in trailing_10
            ),
            "buy_strength_10": round(
                sum(max(0.0, float(item["ratio"])) for item in trailing_10), 3
            ),
            "sell_strength_10": round(
                sum(max(0.0, -float(item["ratio"])) for item in trailing_10), 3
            ),
            "same_days_3": sum(
                item["significant"]
                and _direction(float(item["ratio"])) == last_direction
                for item in trailing_3
            ),
            "last_significant_age": last_age,
            "last_significant_direction": last_direction,
            "signal_date": str((last_significant or {}).get("date") or ""),
            "signal_active_flow": round(
                float((last_significant or {}).get("active_flow") or 0.0), 6
            ),
            "signal_significance_ratio": round(
                float(
                    (last_significant or {}).get("significance_ratio") or 0.0
                ),
                2,
            ),
            "signal_raw_delta_shares": int(
                (last_significant or {}).get("raw_delta_shares") or 0
            ),
            "signal_position_event": str(
                (last_significant or {}).get("position_event") or ""
            ),
            "prior_significant_age": (
                date_index
                - common_dates.index(str(prior_significant["date"]))
                if prior_significant
                else None
            ),
        }
    return result


def _active_participants(features: dict[str, dict], direction: int) -> list[str]:
    return [
        etf
        for etf, feature in features.items()
        if feature["last_significant_direction"] == direction
        and feature["last_significant_age"] is not None
        and int(feature["last_significant_age"]) < SIGNAL_OVERLAP_SESSIONS
    ]


def _supporting_participants(
    features: dict[str, dict], direction: int
) -> list[str]:
    return [
        etf
        for etf, feature in features.items()
        if feature["last_significant_direction"] == direction
        and feature["last_significant_age"] is not None
        and int(feature["last_significant_age"]) < SUPPORT_SESSIONS
        and float(feature["ewma_10"]) * direction > 0
    ]


def _high_information_watch(
    *,
    features: dict[str, dict],
    prior_features: dict[str, dict],
    exited_etfs: set[str],
) -> tuple[str, int, str] | None:
    candidates = []
    for etf, feature in features.items():
        if not feature["significant"]:
            continue
        direction = _direction(float(feature["ratio"]))
        if not direction:
            continue
        event = feature["position_event"]
        prior_10 = float((prior_features.get(etf) or {}).get("ewma_10") or 0.0)
        age = feature["prior_significant_age"]
        kind = ""
        if event == "new_position":
            kind = "reentry" if etf in exited_etfs else "new_position"
        elif event == "full_exit":
            kind = "full_exit"
        elif prior_10 * direction < -0.05:
            kind = "reversal"
        elif age is not None and int(age) >= 5:
            kind = "restart"
        if kind:
            candidates.append(
                (
                    float(feature["significance_ratio"]),
                    etf,
                    direction,
                    kind,
                )
            )
    if not candidates:
        return None
    candidates.sort(reverse=True)
    _, etf, direction, kind = candidates[0]
    return etf, direction, kind


def _transition_label(previous: str, current: str, direction: int) -> str:
    action = "買方" if direction > 0 else "賣方"
    if current in {"buy", "sell"} and previous == "watch":
        return f"觀察升級為{action}共識"
    if current in {"buy", "sell"} and previous in {"buy", "sell"}:
        if previous != current:
            return f"共識反手為{action}"
        return f"{action}共識延續"
    if current in {"buy", "sell"}:
        return f"新形成{action}共識"
    if current == "watch" and previous in {"buy", "sell"}:
        return "共識降溫，退回觀察"
    if current == "watch" and previous == "watch":
        return "高資訊觀察延續"
    if current == "watch":
        return "新增高資訊觀察"
    return "離開有效狀態"


def _compact_card(
    *,
    stock_id: str,
    name: str,
    category: str,
    state: str,
    direction: int,
    score: int,
    components: dict[str, int],
    participants: list[str],
    features: dict[str, dict],
    transition: str,
    watch_kind: str,
    first_seen: str,
    confirmed_date: str,
    last_confirmed: str,
    state_days: int,
) -> dict:
    evidence = []
    for etf in participants:
        feature = features[etf]
        evidence.append(
            {
                "etf": etf,
                "etf_label": ETF_LABEL.get(etf, etf),
                "direction": direction,
                "signal_date": feature["signal_date"],
                "active_flow": feature["signal_active_flow"],
                "significance_ratio": feature["signal_significance_ratio"],
                "raw_delta_shares": feature["signal_raw_delta_shares"],
                "position_event": feature["signal_position_event"],
                "ewma_3": feature["ewma_3"],
                "ewma_10": feature["ewma_10"],
                "ewma_20": feature["ewma_20"],
            }
        )
    return {
        "stock_id": stock_id,
        "name": name,
        "category": category,
        "state": state,
        "direction": direction,
        "score": score,
        "score_label": "觀察成熟度" if state == "watch" else "共識強度",
        "score_components": components,
        "transition": transition,
        "watch_kind": watch_kind,
        "participants": participants,
        "etf_label": "・".join(ETF_LABEL.get(etf, etf) for etf in participants),
        "breadth": len(participants),
        "first_seen_date": first_seen,
        "confirmed_date": confirmed_date,
        "last_confirmed_date": last_confirmed,
        "state_days": state_days,
        "evidence": evidence,
    }


def build_consensus_payload(
    histories: dict[str, dict],
    tags: dict,
    *,
    as_of: str | None = None,
) -> dict:
    etfs = sorted(histories)
    records, dates_by_etf = build_move_store(histories, tags)
    common_dates = _common_dates(dates_by_etf)
    if as_of is not None:
        common_dates = [date for date in common_dates if date <= as_of]
    common_dates = common_dates[-HISTORY_SESSIONS:]
    if not common_dates:
        raise ValueError("V4 requires at least one common ETF disclosure date")
    assign_no_lookahead_thresholds(records, common_dates, etfs)
    stock_ids = sorted(
        {
            stock_id
            for date in common_dates
            for stock_id in records.get(date, {})
        }
    )
    names = {}
    categories = {}
    for date in common_dates:
        for stock_id, by_etf in records.get(date, {}).items():
            if not by_etf:
                continue
            example = next(iter(by_etf.values()))
            names[stock_id] = _display_name(
                stock_id, str(example.get("name") or stock_id)
            )
            categories[stock_id] = str(example.get("category") or "未分類")

    series: dict[str, dict[str, list[dict]]] = defaultdict(dict)
    boards: dict[str, dict[str, list[dict]]] = {}
    audit: dict[str, list[dict]] = defaultdict(list)
    exact_rows = 0
    estimated_rows = 0
    for stock_id in stock_ids:
        per_etf_history: dict[str, list[dict]] = {etf: [] for etf in etfs}
        ewmas = {
            etf: {half_life: 0.0 for half_life in EWMA_HALFLIVES}
            for etf in etfs
        }
        previous_features: dict[str, dict] = {}
        previous_state = "none"
        previous_direction = 0
        first_seen = ""
        confirmed_date = ""
        last_confirmed = ""
        state_days = 0
        exited_etfs: set[str] = set()
        for date_index, date in enumerate(common_dates):
            features = _features_for_date(
                stock_id=stock_id,
                date_index=date_index,
                common_dates=common_dates,
                etfs=etfs,
                records=records,
                histories=per_etf_history,
                ewmas=ewmas,
            )
            for etf, feature in features.items():
                if feature["unit_quality"] == "exact":
                    exact_rows += 1
                elif feature["unit_quality"] == "estimated":
                    estimated_rows += 1
                if feature["position_event"] == "full_exit":
                    exited_etfs.add(etf)

            buy_active = _active_participants(features, 1)
            sell_active = _active_participants(features, -1)
            current_state = "none"
            direction = 0
            participants: list[str] = []
            score = 0
            components: dict[str, int] = {}
            watch_kind = ""

            active_choice = None
            if len(buy_active) >= 2:
                active_choice = (1, buy_active)
            if len(sell_active) >= 2:
                sell_score, _ = _score(features, sell_active, -1)
                buy_score, _ = (
                    _score(features, buy_active, 1)
                    if active_choice
                    else (0, {})
                )
                if not active_choice or sell_score > buy_score:
                    active_choice = (-1, sell_active)
            if active_choice:
                direction, participants = active_choice
                current_state = "buy" if direction > 0 else "sell"
                score, components = _score(features, participants, direction)
            elif previous_state in {"buy", "sell"}:
                direction = previous_direction
                support = _supporting_participants(features, direction)
                support_score, support_components = _score(
                    features, support, direction
                )
                if len(support) >= 2 and support_score >= MAINTENANCE_SCORE:
                    current_state = previous_state
                    participants = support
                    score, components = support_score, support_components
                elif support:
                    current_state = "watch"
                    participants = [
                        max(
                            support,
                            key=lambda etf: abs(float(features[etf]["ewma_10"])),
                        )
                    ]
                    watch_kind = "consensus_cooling"
            if current_state == "none":
                watch = _high_information_watch(
                    features=features,
                    prior_features=previous_features,
                    exited_etfs=exited_etfs,
                )
                if watch:
                    etf, direction, watch_kind = watch
                    current_state = "watch"
                    participants = [etf]
                elif (
                    previous_state == "watch"
                    and previous_direction
                    and audit[stock_id]
                    and int(audit[stock_id][-1].get("watch_age") or 0)
                    < WATCH_SESSIONS - 1
                ):
                    current_state = "watch"
                    direction = previous_direction
                    participants = list(
                        audit[stock_id][-1].get("participants") or []
                    )
                    watch_kind = str(
                        audit[stock_id][-1].get("watch_kind") or ""
                    )

            if current_state == "watch" and participants:
                direction = direction or _direction(
                    float(features[participants[0]]["ewma_3"])
                )
                other_ratio = max(
                    (
                        max(0.0, float(feature["ratio"]) * direction)
                        for etf, feature in features.items()
                        if etf not in participants
                    ),
                    default=0.0,
                )
                score, components = _watch_score(
                    features[participants[0]],
                    other_same_direction_ratio=other_ratio,
                    watch_kind=watch_kind,
                )

            if current_state == previous_state and direction == previous_direction:
                state_days += 1
            elif current_state != "none":
                state_days = 1
                first_seen = date
            else:
                state_days = 0
                first_seen = ""
                confirmed_date = ""
                last_confirmed = ""
            if current_state in {"buy", "sell"}:
                if previous_state != current_state:
                    confirmed_date = date
                last_confirmed = date
            transition = _transition_label(
                previous_state, current_state, direction
            )
            watch_age = (
                int(audit[stock_id][-1].get("watch_age") or 0) + 1
                if current_state == previous_state == "watch"
                else 0
            )
            state_row = {
                "date": date,
                "state": current_state,
                "direction": direction,
                "score": score,
                "participants": participants,
                "watch_kind": watch_kind,
                "watch_age": watch_age,
                "transition": transition,
            }
            audit[stock_id].append(state_row)
            if current_state != "none":
                card = _compact_card(
                    stock_id=stock_id,
                    name=names.get(stock_id, stock_id),
                    category=categories.get(stock_id, "未分類"),
                    state=current_state,
                    direction=direction,
                    score=score,
                    components=components,
                    participants=participants,
                    features=features,
                    transition=transition,
                    watch_kind=watch_kind,
                    first_seen=first_seen,
                    confirmed_date=confirmed_date,
                    last_confirmed=last_confirmed,
                    state_days=state_days,
                )
                lane = (
                    "watching"
                    if current_state == "watch"
                    else "buying"
                    if current_state == "buy"
                    else "selling"
                )
                boards.setdefault(
                    date, {"watching": [], "buying": [], "selling": []}
                )[lane].append(card)
            previous_features = features
            previous_state = current_state
            previous_direction = direction
        series[stock_id] = {
            etf: [
                {
                    "date": row["date"],
                    "ratio": row["ratio"],
                    "active_flow": row["active_flow"],
                    "significance_ratio": row["significance_ratio"],
                    "significant": row["significant"],
                }
                for row in rows[-HISTORY_SESSIONS:]
            ]
            for etf, rows in per_etf_history.items()
        }

    for date in common_dates:
        board = boards.setdefault(
            date, {"watching": [], "buying": [], "selling": []}
        )
        for lane in board:
            board[lane].sort(
                key=lambda card: (-int(card["score"]), card["stock_id"])
            )
    latest = common_dates[-1]
    payload = {
        "schema_version": 1,
        "generated": datetime.now().astimezone().isoformat(timespec="seconds"),
        "as_of": latest,
        "etfs": etfs,
        "dates": common_dates,
        "methodology": {
            "colour_is_hard_state": True,
            "score_is_probability": False,
            "minimum_confirming_etfs": 2,
            "signal_overlap_sessions": SIGNAL_OVERLAP_SESSIONS,
            "support_sessions": SUPPORT_SESSIONS,
            "fixed_ewma_half_lives": list(EWMA_HALFLIVES),
            "single_etf_score_can_confirm": False,
            "ordinary_single_etf_actions_hidden": True,
            "concepts_interpreted": False,
        },
        "signals": boards[latest],
        "boards": boards,
        "series": series,
        "transitions": {
            stock_id: [
                row
                for index, row in enumerate(rows[-HISTORY_SESSIONS:])
                if index == 0
                or row["state"]
                != rows[-HISTORY_SESSIONS:][index - 1]["state"]
                or row["direction"]
                != rows[-HISTORY_SESSIONS:][index - 1]["direction"]
            ]
            for stock_id, rows in audit.items()
        },
        "data_quality": {
            "exact_move_rows": exact_rows,
            "estimated_move_rows": estimated_rows,
            "historical_estimate": "fund_size / rounded NAV",
        },
    }
    return hydrate_board(payload, latest)


def hydrate_board(payload: dict, date: str) -> dict:
    """Attach aligned, per-ETF 20-session series to one historical board."""
    dates = list(payload.get("dates") or [])
    if date not in dates:
        raise ValueError(f"Unknown V4 board date: {date}")
    end = dates.index(date) + 1
    allowed_dates = set(dates[max(0, end - CHART_SESSIONS) : end])
    source = (payload.get("boards") or {}).get(date) or {
        "watching": [],
        "buying": [],
        "selling": [],
    }
    board = {"watching": [], "buying": [], "selling": []}
    for lane, cards in source.items():
        for original in cards:
            card = dict(original)
            stock_series = (payload.get("series") or {}).get(
                str(card.get("stock_id") or ""), {}
            )
            chart_etfs = (
                list(card.get("participants") or [])
                if lane == "watching"
                else list(payload.get("etfs") or [])
            )
            card["etf_trends"] = {
                etf: [
                    row
                    for row in stock_series.get(etf, [])
                    if str(row.get("date") or "") in allowed_dates
                ]
                for etf in chart_etfs
            }
            board[lane].append(card)
    if date == payload.get("as_of"):
        payload["signals"] = board
        return payload
    return {
        **payload,
        "as_of": date,
        "signals": board,
    }
