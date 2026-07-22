#!/usr/bin/env python3
"""Cache the actionable active-ETF buy/hold/sell text used by LINE."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.tag_flow_events import build_event_snapshot  # noqa: E402


SOURCE = ROOT / "data" / "tag_flow.json"
OUT = ROOT / "data" / "etf_action_insight.json"
ETFS = ["00403A", "00981A", "00991A"]

BUY_LIMIT = 4
HOLD_LIMIT = 3
SELL_LIMIT = 3
PHONE_CONTENT_WIDTH = 20

EVENT_PRIORITY = {
    "reentry_position": 0,
    "sell_to_buy": 0,
    "buy_to_sell": 0,
    "full_exit": 0,
    "new_position": 1,
    "trial_position": 2,
    "restart_buy": 2,
    "restart_sell": 2,
}

ETF_SHORT = {
    "00403A": "403",
    "00981A": "981",
    "00991A": "991",
}


def _fresh_priority(event: dict) -> tuple:
    return (
        EVENT_PRIORITY.get(str(event.get("event_type")), 9),
        -int(event.get("breadth") or 0),
        int(event.get("age_sessions") or 0),
        -abs(float(event.get("score") or 0.0)),
    )


def _selected(snapshot: dict) -> dict[str, list[dict]]:
    return {
        "buying": sorted(snapshot["buying"], key=_fresh_priority)[:BUY_LIMIT],
        "holding": list(snapshot["holding"][:HOLD_LIMIT]),
        "selling": sorted(snapshot["selling"], key=_fresh_priority)[:SELL_LIMIT],
    }


def _etf_text(etfs: list[str]) -> str:
    return "、".join(ETF_SHORT.get(etf, etf) for etf in etfs)


def _display_width(text: str) -> int:
    return sum(
        2 if unicodedata.east_asian_width(char) in {"W", "F", "A"} else 1
        for char in text
    )


def _mobile_evidence_lines(event: dict) -> list[str]:
    """Return deliberately short semantic rows; LINE must never split a sentence."""
    event_type = str(event.get("event_type") or "")
    etfs = list(event.get("etfs") or [])
    labels = _etf_text(etfs)
    breadth = int(event.get("breadth") or len(etfs))

    if event_type == "sell_to_buy":
        return ["動作：賣後轉買", f"同步：{breadth}/3 ETF"]
    if event_type == "buy_to_sell":
        return ["動作：買後轉賣", f"同步：{breadth}/3 ETF"]
    if event_type == "reentry_position":
        new_etfs = list(event.get("new_etfs") or [])
        continuing = [etf for etf in etfs if etf not in new_etfs]
        rows = ["動作：重新建倉"]
        rows.extend(f"ETF：{ETF_SHORT.get(etf, etf)} 重納" for etf in new_etfs)
        rows.extend(f"ETF：{ETF_SHORT.get(etf, etf)} 續買" for etf in continuing)
        return rows if len(rows) > 1 else [*rows, "確認：重新納入持股"]
    if event_type in {"new_position", "trial_position"}:
        new_etfs = list(event.get("new_etfs") or etfs)
        label = "試單建倉" if event_type == "trial_position" else "新建倉"
        rows = [f"動作：{label}"]
        rows.extend(f"ETF：{ETF_SHORT.get(etf, etf)} 新納入" for etf in new_etfs)
        return rows
    if event_type == "full_exit":
        exit_etfs = list(event.get("exit_etfs") or etfs)
        rows = ["動作：完全出清"]
        for etf in exit_etfs:
            rows.extend([f"ETF：{ETF_SHORT.get(etf, etf)}", "狀態：移除持股"])
        return rows
    if event_type == "conviction_buy":
        buy_days = int(event.get("buy_days") or 0)
        sell_days = int(event.get("sell_days") or 0)
        return [
            "動作：持續加碼",
            f"10日：{buy_days}買・{sell_days}賣",
            f"參與：{breadth} 檔 ETF",
        ]
    if event_type in {"restart_buy", "restart_sell"}:
        action = "重新買進" if event_type == "restart_buy" else "重新賣出"
        return [f"動作：{action}", f"ETF：{labels}", "確認：沉寂後重啟"]
    return [f"動作：{event.get('event_label') or '持股異動'}", "確認：訊號已成立"]


def _lane(lines: list[str], title: str, events: list[dict], empty: str) -> None:
    lines.append(f"{title}｜{len(events)} 檔")
    if not events:
        lines.append(empty)
        return
    for index, event in enumerate(events, 1):
        category = str(event.get("category") or "未分類")
        stock_id = str(event.get("stock_id") or "").strip()
        identity = f"{event['name']}｜{stock_id}" if stock_id else str(event["name"])
        identity_line = f"{index:02d}. {identity}"
        if _display_width(identity_line) <= PHONE_CONTENT_WIDTH:
            lines.append(identity_line)
        else:
            lines.append(f"{index:02d}. {event['name']}")
            if stock_id:
                lines.append(f"　　代號：{stock_id}")
        category_line = f"　　類股：{category}"
        if _display_width(category_line) <= PHONE_CONTENT_WIDTH:
            lines.append(category_line)
        else:
            lines.extend(["　　類股：", f"　　　{category}"])
        lines.extend(f"　　{row}" for row in _mobile_evidence_lines(event))


def _display_date(as_of: str) -> str:
    try:
        parsed = datetime.strptime(as_of, "%Y-%m-%d")
    except ValueError:
        return as_of
    return f"{parsed.month}月{parsed.day}日"


def render_line_text(as_of: str, selected: dict[str, list[dict]]) -> str:
    lines = ["主動 ETF 動作", "━━━━━━━━━━━━━━", f"截至：{_display_date(as_of)}", ""]
    _lane(lines, "🔴 買進觀察", selected["buying"], "本日無新買進訊號")
    lines.extend(["", "━━━━━━━━━━━━━━"])
    _lane(lines, "🟠 續抱參考", selected["holding"], "本日無續抱確認")
    lines.extend(["", "━━━━━━━━━━━━━━"])
    _lane(lines, "🟢 賣出警示", selected["selling"], "本日無新賣出訊號")
    text = "\n".join(lines).strip()
    for line in text.splitlines():
        if not line or set(line) == {"━"}:
            continue
        width = _display_width(line)
        if width > PHONE_CONTENT_WIDTH:
            raise RuntimeError(
                f"ETF action line exceeds phone width ({width}>{PHONE_CONTENT_WIDTH}): {line}"
            )
    if len(text) > 4500:
        raise RuntimeError(f"ETF action LINE text is unexpectedly long: {len(text)}")
    return text


def build_payload(data: dict) -> dict:
    snapshot = build_event_snapshot(data, ETFS)
    selected = _selected(snapshot)
    return {
        "schema_version": 1,
        "generated": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_generated": data.get("generated"),
        "as_of": snapshot["as_of"],
        "line_text": render_line_text(snapshot["as_of"], selected),
        "signals": selected,
        "concepts_interpreted": False,
    }


def generate(source: Path = SOURCE, out: Path = OUT) -> dict:
    data = json.loads(source.read_text(encoding="utf-8"))
    if data.get("schema_version") != 2:
        raise RuntimeError("tag_flow.json schema_version must be 2")
    payload = build_payload(data)
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--print", action="store_true", dest="print_text")
    args = parser.parse_args()
    payload = generate()
    if args.print_text:
        print(payload["line_text"])
    else:
        counts = {key: len(value) for key, value in payload["signals"].items()}
        print(
            "[etf-action] "
            f"as_of={payload['as_of']} buy={counts['buying']} "
            f"hold={counts['holding']} sell={counts['selling']}"
        )
        print(f"Saved {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
