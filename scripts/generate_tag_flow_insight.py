#!/usr/bin/env python3
"""Generate one decision-focused 類股 insight shared by email and LINE.

The input is the price-drift-free, category-only observation cache produced by
``build_tag_flow.py``.  This script deliberately does not use 概念股 labels.
It identifies sectors that are both net-bought and accelerating, then lists
only stocks bought by all three Taiwan active ETFs in the selected window.

The generated JSON is a cache: the daily email and LINE webhook both read the
same text so their interpretation cannot drift.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
import json
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SOURCE = DATA / "tag_flow.json"
OUT = DATA / "tag_flow_insight.json"

ETFS = ["00403A", "00981A", "00991A"]
LOOKBACK = 5
RECENT_DAYS = 2
MAX_SECTORS = 3
MAX_STOCKS = 5
EPSILON = 1e-6
MIN_STRENGTH = 0.05
MIN_ACCELERATION = 0.005


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _shared_dates(data: dict) -> list[str]:
    by_etf = data.get("dates", {}).get("by_etf", {})
    date_sets = [set(by_etf.get(etf, [])) for etf in ETFS]
    if any(not values for values in date_sets):
        return []
    return sorted(set.intersection(*date_sets))


def _aggregate(data: dict, dates: list[str]) -> list[dict]:
    date_set = set(dates)
    sector_rows: dict[str, dict] = {}

    for observation in data.get("observations", []):
        etf = observation.get("etf")
        date = observation.get("date")
        if etf not in ETFS or date not in date_set:
            continue
        for move in observation.get("stocks", []):
            category = str(move.get("category") or "未分類")
            if category == "未分類":
                continue
            flow = float(move.get("flow") or 0.0)
            stock_id = str(move.get("id") or "")
            sector = sector_rows.setdefault(
                category,
                {
                    "category": category,
                    "by_etf": defaultdict(float),
                    "by_date": defaultdict(float),
                    "stocks": {},
                },
            )
            sector["by_etf"][etf] += flow
            sector["by_date"][date] += flow / len(ETFS)
            stock = sector["stocks"].setdefault(
                stock_id,
                {
                    "id": stock_id,
                    "name": move.get("name") or stock_id,
                    "by_etf": defaultdict(float),
                },
            )
            stock["by_etf"][etf] += flow

    prior_dates = dates[:-RECENT_DAYS]
    recent_dates = dates[-RECENT_DAYS:]
    results: list[dict] = []
    for sector in sector_rows.values():
        by_etf = dict(sector["by_etf"])
        strength = sum(by_etf.get(etf, 0.0) for etf in ETFS) / len(ETFS)
        prior_avg = statistics.fmean(
            sector["by_date"].get(date, 0.0) for date in prior_dates
        )
        recent_avg = statistics.fmean(
            sector["by_date"].get(date, 0.0) for date in recent_dates
        )
        acceleration = recent_avg - prior_avg
        daily = [sector["by_date"].get(date, 0.0) for date in dates]

        stock_pool = []
        for stock in sector["stocks"].values():
            stock_by_etf = dict(stock["by_etf"])
            buyers = sum(stock_by_etf.get(etf, 0.0) > EPSILON for etf in ETFS)
            stock_strength = sum(
                stock_by_etf.get(etf, 0.0) for etf in ETFS
            ) / len(ETFS)
            if buyers == len(ETFS) and stock_strength > EPSILON:
                stock_pool.append(
                    {
                        "id": stock["id"],
                        "name": stock["name"],
                        "strength": round(stock_strength, 4),
                    }
                )
        stock_pool.sort(key=lambda row: -row["strength"])

        results.append(
            {
                "category": sector["category"],
                "strength": round(strength, 4),
                "acceleration": round(acceleration, 4),
                "buyers": sum(by_etf.get(etf, 0.0) > EPSILON for etf in ETFS),
                "buy_days": sum(value > EPSILON for value in daily),
                "latest_positive": daily[-1] > EPSILON,
                "stocks_all_three": stock_pool[:MAX_STOCKS],
            }
        )
    return results


def _sector_reason(row: dict, n_dates: int) -> str:
    if row["buyers"] == 3:
        breadth = "三檔主動 ETF 同步加碼"
    else:
        breadth = "多數主動 ETF 同向加碼"
    persistence = (
        "買盤具持續性" if row["buy_days"] >= max(3, n_dates - 1)
        else "近期買盤轉強"
    )
    return f"{breadth}，{persistence}，而且最近兩日力道高於前三日。"


def _render_line(as_of: str, sectors: list[dict], cooling: dict | None) -> str:
    lines = [
        "🔥 吳大師｜ETF 類股洞察",
        f"截至 {as_of}｜近 5 個共同交易日",
        "",
    ]
    if not sectors:
        lines.extend(
            [
                "目前沒有同時符合『淨加碼＋正在加速＋至少兩檔 ETF 同向』的明確主線。",
                "結論：先觀察，不把單一 ETF 的換股誤認成市場共識。",
            ]
        )
        return "\n".join(lines)

    for index, row in enumerate(sectors, 1):
        lines.append(f"主線 {index}｜{row['category']}：強勢加速")
        lines.append(_sector_reason(row, LOOKBACK))
        names = [stock["name"] for stock in row["stocks_all_three"]]
        if names:
            lines.append("三檔共買池：" + "、".join(names))
        else:
            lines.append("三檔共買池：暫無同一檔個股獲三檔同步加碼")
        lines.append("")

    if cooling:
        lines.append(
            f"降溫提醒｜{cooling['category']}：仍有買盤，但近期力道已放慢。"
        )
        lines.append("")
    pool_names = []
    for row in sectors:
        for stock in row["stocks_all_three"]:
            if stock["name"] not in pool_names:
                pool_names.append(stock["name"])
    if pool_names:
        lines.append("一句話：優先追蹤三檔 ETF 同步買進的「" + "、".join(pool_names[:6]) + "」，其餘只列觀察。")
    else:
        lines.append("一句話：類股方向正在轉強，但尚未形成同一檔個股的三方共識，先觀察。")
    return "\n".join(lines).strip()


def generate() -> dict:
    data = _load(SOURCE)
    if data.get("schema_version") != 2:
        raise RuntimeError("tag_flow.json schema_version must be 2")
    dates = _shared_dates(data)
    if len(dates) < LOOKBACK:
        raise RuntimeError(f"need at least {LOOKBACK} common ETF sessions")
    selected_dates = dates[-LOOKBACK:]
    rows = _aggregate(data, selected_dates)
    candidates = [
        row for row in rows
        if row["strength"] >= MIN_STRENGTH
        and row["acceleration"] >= MIN_ACCELERATION
        and row["latest_positive"]
        and row["buyers"] >= 2
    ]
    candidates.sort(
        key=lambda row: (-row["buyers"], -row["strength"], -row["acceleration"])
    )
    selected = candidates[:MAX_SECTORS]
    cooling_rows = [
        row for row in rows
        if row["strength"] > MIN_STRENGTH
        and row["acceleration"] < -MIN_ACCELERATION
        and row["buyers"] >= 2
    ]
    cooling = max(cooling_rows, key=lambda row: row["strength"], default=None)
    line_text = _render_line(selected_dates[-1], selected, cooling)
    payload = {
        "schema_version": 1,
        "generated": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_generated": data.get("generated"),
        "as_of": selected_dates[-1],
        "window": {
            "dates": selected_dates,
            "recent_days": RECENT_DAYS,
            "comparison_days": LOOKBACK - RECENT_DAYS,
            "etfs": ETFS,
        },
        "methodology": (
            "category-only; strong = positive equal-weight normalized flow; "
            "accelerating = latest 2-session daily average above prior 3; "
            "stock pool requires positive normalized flow from all 3 ETFs"
        ),
        "sectors": selected,
        "cooling": cooling,
        "line_text": line_text,
        "email_text": line_text,
    }
    OUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"[theme-insight] as_of={payload['as_of']} common_sessions={len(selected_dates)} "
        f"strong_accelerating={len(selected)} "
        f"leaders={','.join(row['category'] for row in selected) or 'none'}"
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--print", choices=["line", "email"])
    args = parser.parse_args()
    if args.print:
        payload = _load(OUT)
        print(payload[f"{args.print}_text"])
        return 0
    generate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
