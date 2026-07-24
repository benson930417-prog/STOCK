"""Final active-ETF consensus state engine.

V4 separates *state* from *score*:

* yellow/watch = one manager produced a high-information precursor;
* red/buy = at least two managers independently confirmed buying;
* green/sell = at least two managers independently confirmed selling.

The colour is a hard gate.  A large one-manager score can never become a
red/green conclusion.  The score only ranks maturity inside the same lane.
V4 assigns its own ETF-wide, preceding-10-session usual-action scale without
look-ahead; concept tags are never interpreted.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import math
from statistics import median

from src.etf_intent_v3 import (
    ETF_LABEL,
    _common_dates,
    _display_name,
    build_move_store,
)


HISTORY_SESSIONS = 260
CHART_SESSIONS = 20
SIGNAL_OVERLAP_SESSIONS = 3
SUPPORT_SESSIONS = 10
WATCH_SESSIONS = SUPPORT_SESSIONS
MAINTENANCE_SCORE = 40
CORE_SCORE = 60
CORE_FRESHNESS = 8
CORE_RELATIVE_STRENGTH = 8
CORE_PERSISTENCE = 10
CORE_FRESH_STATE_DAYS = 3
ACTION_BASELINE_SESSIONS = 10
MIN_BASELINE_OBSERVATIONS = 8
MIN_ACTIVE_FLOW = 0.02
SIGNIFICANCE_GATE_FRACTION = 0.60
MIN_WATCH_ENTRY_RATIO = SIGNIFICANCE_GATE_FRACTION
MIN_WATCH_EXIT_RATIO = 1.00
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


def assign_v4_action_scales(
    records: dict[str, dict[str, dict[str, dict]]],
    common_dates: list[str],
    etfs: list[str],
) -> None:
    """Attach a no-look-ahead ETF-wide usual-action scale to each move.

    The previous V4 divided a sparse ETF/stock move by that stock's 0.02%
    fallback significance gate.  That made an ordinary action look like
    ``23.9x``.  V4 now gives the two concepts separate fields:

    * ``normal_action_multiple`` compares the move with the ETF's median
      non-zero same-direction trade during the preceding 10 common sessions;
    * ``significance_gate`` is only the qualification line (60% of usual,
      never below 0.02% of ETF size).

    Direction-specific history is preferred; the combined buy/sell sample is
    used until there are enough same-direction observations.  The current date
    is never included in its own baseline.
    """
    fallback_typical = MIN_ACTIVE_FLOW / SIGNIFICANCE_GATE_FRACTION
    for date_index, date in enumerate(common_dates):
        prior_dates = common_dates[
            max(0, date_index - ACTION_BASELINE_SESSIONS) : date_index
        ]
        for etf in etfs:
            buy_sample: list[float] = []
            sell_sample: list[float] = []
            for prior_date in prior_dates:
                for by_etf in records.get(prior_date, {}).values():
                    prior_move = by_etf.get(etf)
                    if not prior_move or not prior_move.get("copyable"):
                        continue
                    flow = float(prior_move.get("active_flow") or 0.0)
                    if flow > 1e-9:
                        buy_sample.append(flow)
                    elif flow < -1e-9:
                        sell_sample.append(abs(flow))
            combined_sample = buy_sample + sell_sample
            for by_etf in records.get(date, {}).values():
                move = by_etf.get(etf)
                if move is None:
                    continue
                flow = float(move.get("active_flow") or 0.0)
                magnitude = abs(flow) if move.get("copyable") else 0.0
                directional = buy_sample if flow > 0 else sell_sample
                if len(directional) >= MIN_BASELINE_OBSERVATIONS:
                    sample = directional
                    source = "same_direction"
                elif len(combined_sample) >= MIN_BASELINE_OBSERVATIONS:
                    sample = combined_sample
                    source = "combined"
                else:
                    sample = []
                    source = "fallback"
                typical = median(sample) if sample else fallback_typical
                gate = max(MIN_ACTIVE_FLOW, typical * SIGNIFICANCE_GATE_FRACTION)
                multiple = magnitude / typical if typical else 0.0
                money_twd = abs(float(move.get("money_twd") or 0.0))
                typical_money_twd = (
                    money_twd * typical / magnitude if magnitude > 0 else 0.0
                )
                move["v4_baseline_sessions"] = len(prior_dates)
                move["v4_baseline_observations"] = len(sample)
                move["v4_baseline_source"] = source
                move["normal_action_flow"] = round(typical, 6)
                move["significance_gate"] = round(gate, 6)
                move["normal_action_multiple"] = round(multiple, 3)
                # Compatibility alias for older cache readers.  Its V4 meaning
                # is now "times usual action", never "times gate".
                move["significance_ratio"] = round(multiple, 3)
                move["estimated_money_yi"] = round(money_twd / 1e8, 3)
                move["normal_action_money_yi"] = round(
                    typical_money_twd / 1e8, 3
                )
                move["significant"] = bool(
                    move.get("copyable") and magnitude >= gate
                )


def _clipped_ratio(move: dict | None) -> float:
    if not move or not move.get("copyable"):
        return 0.0
    typical = float(move.get("normal_action_flow") or 0.0)
    flow = float(move.get("active_flow") or 0.0)
    if typical <= 0:
        return 0.0
    return max(-3.0, min(3.0, flow / typical))


def _score(
    features: dict[str, dict],
    participants: list[str],
    direction: int,
) -> tuple[int, dict[str, int]]:
    """Score confirmed consensus; never decides whether consensus exists."""
    breadth = len(participants)
    breadth_points = 35 if breadth >= 3 else 30 if breadth >= 2 else 0
    counts = sorted(
        (
            int(features[etf]["buy_days_10" if direction > 0 else "sell_days_10"])
            for etf in participants
        ),
        reverse=True,
    )
    weaker_count = counts[1] if len(counts) >= 2 else 0
    persistence = round(25 * min(weaker_count, 5) / 5)
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
    strength = round(20 * min(weaker_strength / 5.0, 1.0))
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
    alignment = (2 if short_aligned else 0) + (
        3 if background_aligned else 0
    )
    ages = sorted(
        int(features[etf].get("last_significant_age") or 0)
        for etf in participants
        if features[etf].get("last_significant_age") is not None
    )
    consensus_age = ages[1] if len(ages) >= 2 else SUPPORT_SESSIONS
    freshness = round(
        15
        * max(0.0, (SUPPORT_SESSIONS - 1 - consensus_age))
        / (SUPPORT_SESSIONS - 1)
    )
    components = {
        "independent_etfs": breadth_points,
        "joint_persistence": persistence,
        "relative_strength": strength,
        "freshness": freshness,
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
    current_structural = feature["position_event"] in STRUCTURAL_EVENTS
    magnitude_ratio = (
        float(feature["normal_action_multiple"])
        if current_structural
        else float(feature["signal_significance_ratio"])
    )
    magnitude = min(20, round(20 * min(magnitude_ratio / 3, 1)))
    repeat = min(15, int(feature["same_days_3"]) * 6)
    latent = min(10, round(10 * min(other_same_direction_ratio, 1)))
    age = (
        0
        if current_structural
        else int(feature.get("last_significant_age") or 0)
    )
    freshness = round(
        15
        * max(0.0, SUPPORT_SESSIONS - 1 - age)
        / (SUPPORT_SESSIONS - 1)
    )
    components = {
        "event_quality": event_points,
        "relative_size": magnitude,
        "repeat_action": repeat,
        "latent_second_etf": latent,
        "freshness": freshness,
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
            "normal_action_flow": round(
                float((move or {}).get("normal_action_flow") or 0.0), 6
            ),
            "significance_gate": round(
                float((move or {}).get("significance_gate") or 0.0), 6
            ),
            "normal_action_multiple": round(
                float((move or {}).get("normal_action_multiple") or 0.0), 3
            ),
            "significance_ratio": round(
                float((move or {}).get("normal_action_multiple") or 0.0), 3
            ),
            "significant": bool((move or {}).get("significant")),
            "copyable": bool((move or {}).get("copyable")),
            "position_event": str((move or {}).get("position_event") or ""),
            "raw_delta_shares": int((move or {}).get("raw_delta_shares") or 0),
            "money_twd": float((move or {}).get("money_twd") or 0.0),
            "estimated_money_yi": round(
                float((move or {}).get("estimated_money_yi") or 0.0), 3
            ),
            "normal_action_money_yi": round(
                float((move or {}).get("normal_action_money_yi") or 0.0), 3
            ),
            "baseline_observations": int(
                (move or {}).get("v4_baseline_observations") or 0
            ),
            "baseline_source": str(
                (move or {}).get("v4_baseline_source") or ""
            ),
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
        net_active_flow_10 = sum(
            float(item["active_flow"]) for item in trailing_10 if item["copyable"]
        )
        net_ratio_10 = sum(
            float(item["ratio"]) for item in trailing_10 if item["copyable"]
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
            "net_active_flow_10": round(net_active_flow_10, 6),
            "net_ratio_10": round(net_ratio_10, 3),
            "net_direction_10": _direction(net_active_flow_10),
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
                    (last_significant or {}).get("normal_action_multiple") or 0.0
                ),
                3,
            ),
            "signal_normal_action_multiple": round(
                float(
                    (last_significant or {}).get("normal_action_multiple") or 0.0
                ),
                3,
            ),
            "signal_estimated_money_yi": round(
                float((last_significant or {}).get("estimated_money_yi") or 0.0),
                3,
            ),
            "signal_normal_action_money_yi": round(
                float(
                    (last_significant or {}).get("normal_action_money_yi") or 0.0
                ),
                3,
            ),
            "signal_significance_gate": round(
                float((last_significant or {}).get("significance_gate") or 0.0),
                6,
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
            "support_sessions_remaining": (
                max(0, SUPPORT_SESSIONS - 1 - int(last_age))
                if last_age is not None
                else 0
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
        and int(feature["net_direction_10"]) == direction
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
        and int(feature["net_direction_10"]) == direction
    ]


def _high_information_watch(
    *,
    features: dict[str, dict],
    prior_features: dict[str, dict],
    exited_etfs: set[str],
) -> tuple[str, int, str] | None:
    candidates = []
    for etf, feature in features.items():
        direction = _direction(float(feature["ratio"]))
        if not feature["copyable"] or not direction:
            continue
        event = feature["position_event"]
        significance_ratio = float(feature["normal_action_multiple"])
        prior_10 = float((prior_features.get(etf) or {}).get("ewma_10") or 0.0)
        age = feature["prior_significant_age"]
        kind = ""
        if event == "new_position":
            if significance_ratio < MIN_WATCH_ENTRY_RATIO:
                continue
            kind = "reentry" if etf in exited_etfs else "new_position"
        elif event == "full_exit":
            if significance_ratio < MIN_WATCH_EXIT_RATIO:
                continue
            kind = "full_exit"
        elif feature["significant"] and prior_10 * direction < -0.05:
            kind = "reversal"
        elif feature["significant"] and age is not None and int(age) >= 5:
            kind = "restart"
        if kind:
            candidates.append(
                (
                    float(feature["normal_action_multiple"]),
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


def _decision_priority(
    *,
    state: str,
    score: int,
    components: dict[str, int],
    participants: list[str],
    state_days: int,
) -> tuple[str, str]:
    """Separate actionable consensus from valid but lower-priority tracking.

    The hard red/green state still answers whether consensus exists.  This
    stricter layer answers which confirmed names deserve attention first:
    the second manager must still be fresh and meaningful, while confirmation
    must either be repeated or be a genuinely strong new formation.
    """
    if state not in {"buy", "sell"}:
        return "watch", "單一 ETF 前兆，尚未形成共識"
    freshness = int(components.get("freshness") or 0)
    strength = int(components.get("relative_strength") or 0)
    persistence = int(components.get("joint_persistence") or 0)
    alignment = int(components.get("horizon_alignment") or 0)
    fresh_formation = (
        state_days <= CORE_FRESH_STATE_DAYS
        and freshness >= 12
        and strength >= CORE_RELATIVE_STRENGTH
    )
    repeated_confirmation = persistence >= CORE_PERSISTENCE
    is_core = (
        score >= CORE_SCORE
        and freshness >= CORE_FRESHNESS
        and strength >= CORE_RELATIVE_STRENGTH
        and alignment >= 3
        and (fresh_formation or repeated_confirmation)
    )
    if not is_core:
        return "tracking", "共識有效，但第二經理人力道、持續或新鮮度尚未達核心"
    if len(participants) >= 3:
        return "core", "3 ETF 共識，且新鮮度與相對力道達標"
    if fresh_formation and not repeated_confirmation:
        return "core", "剛形成的雙 ETF 強確認"
    return "core", "雙 ETF 重複確認且目前仍有力道"


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
        use_current_structural = bool(
            state == "watch"
            and watch_kind in {"new_position", "reentry", "full_exit"}
            and feature["position_event"] in STRUCTURAL_EVENTS
            and _direction(float(feature["active_flow"])) == direction
        )
        evidence.append(
            {
                "etf": etf,
                "etf_label": ETF_LABEL.get(etf, etf),
                "direction": direction,
                "signal_date": (
                    feature["date"]
                    if use_current_structural
                    else feature["signal_date"]
                ),
                "active_flow": (
                    feature["active_flow"]
                    if use_current_structural
                    else feature["signal_active_flow"]
                ),
                "normal_action_multiple": (
                    feature["normal_action_multiple"]
                    if use_current_structural
                    else feature["signal_normal_action_multiple"]
                ),
                "significance_ratio": (
                    feature["normal_action_multiple"]
                    if use_current_structural
                    else feature["signal_normal_action_multiple"]
                ),
                "estimated_money_yi": (
                    feature["estimated_money_yi"]
                    if use_current_structural
                    else feature["signal_estimated_money_yi"]
                ),
                "normal_action_money_yi": (
                    feature["normal_action_money_yi"]
                    if use_current_structural
                    else feature["signal_normal_action_money_yi"]
                ),
                "raw_delta_shares": (
                    feature["raw_delta_shares"]
                    if use_current_structural
                    else feature["signal_raw_delta_shares"]
                ),
                "position_event": (
                    feature["position_event"]
                    if use_current_structural
                    else feature["signal_position_event"]
                ),
                "last_action_age": (
                    0
                    if use_current_structural
                    else feature["last_significant_age"]
                ),
                "support_sessions_remaining": (
                    SUPPORT_SESSIONS - 1
                    if use_current_structural
                    else feature["support_sessions_remaining"]
                ),
                "net_active_flow_10": feature["net_active_flow_10"],
                "net_direction_10": feature["net_direction_10"],
                "ewma_3": feature["ewma_3"],
                "ewma_10": feature["ewma_10"],
                "ewma_20": feature["ewma_20"],
            }
        )
    evidence_ages = sorted(
        int(item["last_action_age"])
        for item in evidence
        if item["last_action_age"] is not None
    )
    expiry_age = (
        evidence_ages[1]
        if state in {"buy", "sell"} and len(evidence_ages) >= 2
        else evidence_ages[0]
        if evidence_ages
        else SUPPORT_SESSIONS
    )
    valid_sessions_remaining = max(
        0, SUPPORT_SESSIONS - 1 - expiry_age
    )
    decision_tier, decision_reason = _decision_priority(
        state=state,
        score=score,
        components=components,
        participants=participants,
        state_days=state_days,
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
        "decision_tier": decision_tier,
        "decision_reason": decision_reason,
        "valid_sessions_remaining": valid_sessions_remaining,
        "freshness_rule": "無新顯著同向動作時每日減分",
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
    assign_v4_action_scales(records, common_dates, etfs)
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
                    and any(
                        int(features.get(etf, {}).get("net_direction_10") or 0)
                        == previous_direction
                        for etf in (
                            audit[stock_id][-1].get("participants") or []
                        )
                    )
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
                    "normal_action_flow": row["normal_action_flow"],
                    "significance_gate": row["significance_gate"],
                    "normal_action_multiple": row["normal_action_multiple"],
                    "significance_ratio": row["significance_ratio"],
                    "significant": row["significant"],
                    "estimated_money_yi": row["estimated_money_yi"],
                    "normal_action_money_yi": row["normal_action_money_yi"],
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
        "schema_version": 3,
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
            "action_baseline_sessions": ACTION_BASELINE_SESSIONS,
            "action_baseline": "ETF-wide median non-zero same-direction action",
            "significance_gate_fraction": SIGNIFICANCE_GATE_FRACTION,
            "display_multiple_is_gate_multiple": False,
            "support_requires_positive_10d_derivative": True,
            "quiet_sessions_reduce_score": True,
            "core_decision_rule": {
                "minimum_score": CORE_SCORE,
                "minimum_freshness_points": CORE_FRESHNESS,
                "minimum_relative_strength_points": CORE_RELATIVE_STRENGTH,
                "repeated_confirmation_points": CORE_PERSISTENCE,
                "fresh_formation_max_state_days": CORE_FRESH_STATE_DAYS,
            },
            "fixed_ewma_half_lives": list(EWMA_HALFLIVES),
            "single_etf_score_can_confirm": False,
            "ordinary_single_etf_actions_hidden": True,
            "minimum_watch_entry_ratio": MIN_WATCH_ENTRY_RATIO,
            "minimum_watch_exit_ratio": MIN_WATCH_EXIT_RATIO,
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
