#!/usr/bin/env python3
"""Cache the actionable active-ETF buy/hold/sell evidence used by LINE."""
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

PHONE_CONTENT_WIDTH = 20

ETF_SHORT = {
    "00403A": "403",
    "00981A": "981",
    "00991A": "991",
}


def _selected(snapshot: dict) -> dict[str, list[dict]]:
    """Keep the complete qualified board; the engine already owns its limits."""
    return {
        "buying": list(snapshot["buying"]),
        "holding": list(snapshot["holding"]),
        "selling": list(snapshot["selling"]),
    }


def _etf_text(etfs: list[str]) -> str:
    return "・".join(ETF_SHORT.get(etf, etf) for etf in etfs)


def _display_width(text: str) -> int:
    return sum(
        2 if unicodedata.east_asian_width(char) in {"W", "F", "A"} else 1
        for char in text
    )


def _fallback_evidence_parts(event: dict) -> list[str]:
    """Support older cached fixtures while the engine owns new display metadata."""
    event_type = str(event.get("event_type") or "")
    etfs = list(event.get("etfs") or [])
    if event_type == "sell_to_buy":
        return ["先前明顯減碼", "現在轉為買進"]
    if event_type == "buy_to_sell":
        return ["先前明顯加碼", "現在轉為賣出"]
    if event_type == "reentry_position":
        new_etfs = list(event.get("new_etfs") or [])
        continuing = [etf for etf in etfs if etf not in new_etfs]
        parts = [f"{ETF_SHORT.get(etf, etf)} 重納" for etf in new_etfs]
        parts.extend(f"{ETF_SHORT.get(etf, etf)} 續買" for etf in continuing)
        return parts or ["曾出清後重納"]
    if event_type in {"new_position", "trial_position"}:
        new_etfs = list(event.get("new_etfs") or etfs)
        label = "小額新納入" if event_type == "trial_position" else "新納入"
        return [f"{ETF_SHORT.get(etf, etf)} {label}" for etf in new_etfs]
    if event_type == "full_exit":
        exit_etfs = list(event.get("exit_etfs") or etfs)
        return [f"{ETF_SHORT.get(etf, etf)} 移除持股" for etf in exit_etfs]
    if event_type == "conviction_buy":
        buy_days = int(event.get("buy_days") or 0)
        sell_days = int(event.get("sell_days") or 0)
        return [f"10日{buy_days}買・{sell_days}賣", "最新仍買"]
    if event_type in {"restart_buy", "restart_sell"}:
        action = "重新買進" if event_type == "restart_buy" else "重新賣出"
        return ["沉寂至少5日", f"現在{action}"]
    return ["訊號已成立"]


def _fallback_qualification(event: dict) -> str:
    event_type = str(event.get("event_type") or "")
    breadth = int(event.get("breadth") or len(event.get("etfs") or []))
    if event_type == "conviction_buy":
        return f"持續（{breadth}檔）"
    if breadth >= 2:
        return f"共識（{breadth}/3）"
    exception = "出清" if event_type == "full_exit" else "建倉"
    if event_type in {"sell_to_buy", "buy_to_sell"}:
        exception = "反轉2日"
    if exception == "反轉2日":
        return "1/3 反轉2日"
    return f"1/3 {exception}例外"


def _mobile_fields(event: dict) -> list[tuple[str, list[str]]]:
    """Return the same five fields for every lane and every event type."""
    etfs = list(event.get("etfs") or [])
    evidence = list(event.get("evidence_parts") or _fallback_evidence_parts(event))
    return [
        ("類股", [str(event.get("category") or "未分類")]),
        ("動作", [str(event.get("event_label") or "持股異動")]),
        ("ETF", [str(event.get("etf_label") or _etf_text(etfs) or "未提供")]),
        (
            "判定",
            [str(event.get("qualification_label") or _fallback_qualification(event))],
        ),
        ("依據", evidence or ["訊號已成立"]),
    ]


def _append_field(lines: list[str], label: str, values: list[str]) -> None:
    """Place complete semantic values on deliberate rows; never word-wrap them."""
    values = [str(value).strip() for value in values if str(value).strip()]
    if not values:
        values = ["未提供"]
    joined = "・".join(values)
    inline = f"  {label}：{joined}"
    if _display_width(inline) <= PHONE_CONTENT_WIDTH:
        lines.append(inline)
        return
    lines.append(f"  {label}：")
    for value in values:
        row = f"    {value}"
        if _display_width(row) > PHONE_CONTENT_WIDTH:
            raise RuntimeError(
                f"ETF action semantic value exceeds phone width: {label}={value}"
            )
        lines.append(row)


def _lane(lines: list[str], title: str, events: list[dict], empty: str) -> None:
    lines.append(f"{title}｜{len(events)} 檔")
    if not events:
        lines.append(empty)
        return
    for index, event in enumerate(events, 1):
        stock_id = str(event.get("stock_id") or "").strip()
        identity = f"{event['name']}｜{stock_id}" if stock_id else str(event["name"])
        identity_line = f"{index:02d}. {identity}"
        if _display_width(identity_line) <= PHONE_CONTENT_WIDTH:
            lines.append(identity_line)
        else:
            lines.append(f"{index:02d}. {event['name']}")
            if stock_id:
                lines.append(f"  代號：{stock_id}")
        for label, values in _mobile_fields(event):
            _append_field(lines, label, values)


def _display_date(as_of: str) -> str:
    try:
        parsed = datetime.strptime(as_of, "%Y-%m-%d")
    except ValueError:
        return as_of
    return f"{parsed.month}月{parsed.day}日"


def render_line_text(as_of: str, selected: dict[str, list[dict]]) -> str:
    lines = [
        "主動 ETF 動作",
        "━━━━━━━━━━━━━━",
        f"截至：{_display_date(as_of)}",
        "判定規則",
        "一般：至少 2/3 同向",
        "1/3：只留建倉・出清",
        "1/3：或反轉連續 2 日",
        "續抱：至少 2 檔參與",
        "",
    ]
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
        "schema_version": 2,
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
