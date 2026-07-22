#!/usr/bin/env python3
"""Cache the actionable active-ETF buy/hold/sell text used by LINE."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

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


def _mobile_evidence(event: dict) -> str:
    """Return one concise action/evidence row beneath a numbered stock."""
    event_type = str(event.get("event_type") or "")
    etfs = list(event.get("etfs") or [])
    labels = _etf_text(etfs)
    badge = _signal_badge(event)

    if event_type == "sell_to_buy":
        return f"賣後轉買｜{badge}"
    if event_type == "buy_to_sell":
        return f"買後轉賣｜{badge}"
    if event_type == "reentry_position":
        new_etfs = list(event.get("new_etfs") or [])
        continuing = [etf for etf in etfs if etf not in new_etfs]
        pieces = []
        if new_etfs:
            pieces.append(f"{_etf_text(new_etfs)}重納")
        if continuing:
            pieces.append(f"{_etf_text(continuing)}續買")
        return f"重新建倉｜{'・'.join(pieces) or '重新納入持股'}"
    if event_type in {"new_position", "trial_position"}:
        new_etfs = list(event.get("new_etfs") or etfs)
        action = "小部位新納入" if event_type == "trial_position" else "新納入"
        label = "試單建倉" if event_type == "trial_position" else "新建倉"
        return f"{label}｜{_etf_text(new_etfs)} {action}"
    if event_type == "full_exit":
        exit_etfs = list(event.get("exit_etfs") or etfs)
        return f"完全出清｜{_etf_text(exit_etfs)} 移除持股"
    if event_type == "conviction_buy":
        buy_days = int(event.get("buy_days") or 0)
        sell_days = int(event.get("sell_days") or 0)
        return f"持續加碼｜10 日 {buy_days}買{sell_days}賣｜{badge}"
    if event_type in {"restart_buy", "restart_sell"}:
        action = "重新買進" if event_type == "restart_buy" else "重新賣出"
        return f"{action}｜{labels} 沉寂後重啟"
    evidence = str(event.get("reason") or "持股動作已確認")
    return f"{event.get('event_label') or '動作確認'}｜{evidence}"


def _signal_badge(event: dict) -> str:
    event_type = str(event.get("event_type") or "")
    breadth = int(event.get("breadth") or len(event.get("etfs") or []))
    if event_type == "conviction_buy":
        return f"{breadth} 檔參與" if breadth else "仍在買"
    if event_type in {"reentry_position", "new_position", "trial_position", "full_exit"}:
        return ""
    return f"{breadth}/3 ETF 同步" if breadth else ""


def _lane(lines: list[str], title: str, events: list[dict], empty: str) -> None:
    lines.append(f"{title}｜{len(events)} 檔")
    if not events:
        lines.append(empty)
        return
    for index, event in enumerate(events, 1):
        category = str(event.get("category") or "未分類")
        stock_id = str(event.get("stock_id") or "").strip()
        metadata = f"{stock_id}｜{category}" if stock_id else category
        lines.append(f"{index:02d}. {event['name']}（{metadata}）")
        lines.append(f"　　{_mobile_evidence(event)}")


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
