#!/usr/bin/env python3
"""Build the daily observation store used by the ETF theme-flow tab.

The first version of this feature pre-aggregated only ``today`` and ``5d``.
That made the UI fast, but also made every other analysis window impossible.
This builder now stores one price-drift-free observation per ETF/session.  The
Streamlit tab can therefore aggregate any available date range without network
access and without rebuilding the data.

Signal (the same measure used by ``src/ui/etf_tab.py``)::

    ActiveWeight = delta_shares * (weight_pct / shares)
                 = money traded on the stock / fund size * 100

The result is a portfolio-weight-equivalent percentage-point flow.  Positive is
an active buy and negative is an active sell; changes caused only by the stock
price do not appear.

For context, every daily stock move is ranked against that ETF's *prior*
20-session distribution of absolute trade sizes.  Empirical percentiles are
used instead of mean-plus-sigma thresholds because trade sizes are skewed and a
single huge rebalance otherwise moves the definition of "large" too much.
Nothing from the current day enters its own reference distribution.
"""
from __future__ import annotations

import bisect
import json
import math
import statistics
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = DATA / "tag_flow.json"
TAGS_FILE = DATA / "stock_tags.json"

ETFS = {
    "00403A": DATA / "etf_00403A_history.json",
    "00981A": DATA / "etf_00981A_history.json",
    "00991A": DATA / "etf_00991A_history.json",
}

BASELINE_SESSIONS = 20
MIN_BASELINE_TRADES = 20
UNTAGGED = "未分類"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def per_share_weight(holding: dict) -> float:
    shares = holding.get("shares") or 0
    return (holding.get("weight_pct", 0.0) / shares) if shares else 0.0


def flow_between(cur_day: dict, base_day: dict) -> dict[str, dict]:
    """Return normalized and estimated-cash flow between two disclosures."""
    cur = {str(h["id"]): h for h in cur_day.get("holdings", [])}
    base = {str(h["id"]): h for h in base_day.get("holdings", [])}
    cur_fund_size = float(cur_day.get("meta", {}).get("fund_size") or 0.0)
    base_fund_size = float(base_day.get("meta", {}).get("fund_size") or 0.0)
    out: dict[str, dict] = {}

    for stock_id, current in cur.items():
        previous = base.get(stock_id)
        if previous is None:
            flow = float(current.get("weight_pct", 0.0))
            out[stock_id] = {
                "name": current.get("name", stock_id),
                "flow": flow,
                "money_twd": flow / 100.0 * cur_fund_size if cur_fund_size else None,
                "dshares": int(current.get("shares", 0) or 0),
            }
            continue

        delta_shares = int(current.get("shares", 0) or 0) - int(
            previous.get("shares", 0) or 0
        )
        if delta_shares == 0:
            continue
        per_weight = (
            per_share_weight(current)
            if current.get("shares")
            else per_share_weight(previous)
        )
        flow = delta_shares * per_weight
        out[stock_id] = {
            "name": current.get("name", previous.get("name", stock_id)),
            "flow": flow,
            "money_twd": flow / 100.0 * cur_fund_size if cur_fund_size else None,
            "dshares": delta_shares,
        }

    for stock_id, previous in base.items():
        if stock_id not in cur:
            flow = -float(previous.get("weight_pct", 0.0))
            out[stock_id] = {
                "name": previous.get("name", stock_id),
                "flow": flow,
                "money_twd": flow / 100.0 * base_fund_size if base_fund_size else None,
                "dshares": -int(previous.get("shares", 0) or 0),
            }
    return out


def _quantile(sorted_values: list[float], q: float) -> float:
    """Small dependency-free linear quantile, equivalent to common defaults."""
    if not sorted_values:
        return 0.0
    pos = (len(sorted_values) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return sorted_values[lo]
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def baseline_summary(sample: list[float]) -> dict:
    ordered = sorted(sample)
    if not ordered:
        return {"n": 0, "median": 0.0, "p80": 0.0, "p95": 0.0}
    return {
        "n": len(ordered),
        "median": round(statistics.median(ordered), 4),
        "p80": round(_quantile(ordered, 0.80), 4),
        "p95": round(_quantile(ordered, 0.95), 4),
    }


def empirical_percentile(value: float, sample: list[float]) -> float | None:
    if len(sample) < MIN_BASELINE_TRADES:
        return None
    ordered = sorted(sample)
    return round(100.0 * bisect.bisect_right(ordered, abs(value)) / len(ordered), 1)


def move_label(flow: float, percentile: float | None) -> str:
    direction = "加碼" if flow > 0 else "減碼"
    if percentile is None:
        return direction
    if percentile >= 95:
        return f"異常{direction}"
    if percentile >= 80:
        return f"明顯{direction}"
    return direction


def build_observations(etf: str, history: dict, tags: dict) -> list[dict]:
    dates = sorted(history)
    pair_flows = [
        flow_between(history[cur], history[prev])
        for prev, cur in zip(dates, dates[1:])
    ]
    observations: list[dict] = []

    for pair_index, (prev_date, date) in enumerate(zip(dates, dates[1:])):
        # Only completed sessions before this observation are allowed into its
        # baseline.  Flatten moves across the most recent N sessions.
        first_prior_pair = max(0, pair_index - BASELINE_SESSIONS)
        prior_magnitudes = [
            abs(move["flow"])
            for prior in pair_flows[first_prior_pair:pair_index]
            for move in prior.values()
            if abs(move["flow"]) > 1e-9
        ]
        baseline = baseline_summary(prior_magnitudes)
        rows = []
        for stock_id, move in pair_flows[pair_index].items():
            flow = float(move["flow"])
            if abs(flow) <= 1e-9:
                continue
            tag = tags.get(stock_id, {})
            percentile = empirical_percentile(flow, prior_magnitudes)
            concepts = [
                concept.get("name", "").strip()
                for concept in tag.get("concepts", [])
                if concept.get("name", "").strip()
            ]
            rows.append(
                {
                    "id": stock_id,
                    "name": tag.get("name") or move["name"],
                    "category": tag.get("category") or UNTAGGED,
                    "group": tag.get("group") or "",
                    "concepts": concepts,
                    "flow": round(flow, 4),
                    "money_twd": (
                        round(float(move["money_twd"]), 0)
                        if move.get("money_twd") is not None
                        else None
                    ),
                    "dshares": move["dshares"],
                    "percentile": percentile,
                    "label": move_label(flow, percentile),
                }
            )
        rows.sort(key=lambda row: -abs(row["flow"]))
        observations.append(
            {
                "etf": etf,
                "date": date,
                "prev_date": prev_date,
                "fund_size": history[date].get("meta", {}).get("fund_size"),
                "baseline": baseline,
                "stocks": rows,
            }
        )
    return observations


def main() -> int:
    tags = load_json(TAGS_FILE).get("tags", {})
    histories = {
        etf: load_json(path)
        for etf, path in ETFS.items()
        if path.exists()
    }
    histories = {etf: hist for etf, hist in histories.items() if len(hist) >= 2}
    if not histories:
        print("no ETF histories found")
        return 1

    observations = [
        observation
        for etf, history in histories.items()
        for observation in build_observations(etf, history, tags)
    ]
    observations.sort(key=lambda item: (item["date"], item["etf"]))

    dates_by_etf = {
        etf: [row["date"] for row in observations if row["etf"] == etf]
        for etf in histories
    }
    common_dates = sorted(
        set.intersection(*(set(dates) for dates in dates_by_etf.values()))
    )

    payload = {
        "schema_version": 2,
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "etfs": list(histories),
        "dates": {
            "by_etf": dates_by_etf,
            "common": common_dates,
            "latest": max(row["date"] for row in observations),
        },
        "methodology": {
            "signal": "ActiveWeight = delta shares * current weight / current shares",
            "unit": "portfolio-weight-equivalent percentage points",
            "cash_estimate": "ActiveWeight / 100 * disclosed fund size (TWD)",
            "baseline_sessions": BASELINE_SESSIONS,
            "notable_percentile": 80,
            "outlier_percentile": 95,
        },
        "observations": observations,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"wrote {OUT}  observations={len(observations)} "
        f"common_sessions={len(common_dates)} latest={payload['dates']['latest']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
