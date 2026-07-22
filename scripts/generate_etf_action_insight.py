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


def _lane(lines: list[str], title: str, events: list[dict], empty: str) -> None:
    lines.append(title)
    if not events:
        lines.append(empty)
        return
    for event in events:
        category = str(event.get("category") or "未分類")
        lines.append(
            f"• {event['name']}（{category}）｜{event['event_label']}"
        )
        lines.append(
            f"  {event['reason']}｜{event['confirmation_label']}"
        )


def render_line_text(as_of: str, selected: dict[str, list[dict]]) -> str:
    lines = [f"🔥 主動 ETF 買／抱／賣｜截至 {as_of}", ""]
    _lane(lines, "🔴 買進觀察", selected["buying"], "目前沒有新的買進訊號。")
    lines.append("")
    _lane(lines, "🟠 續抱參考", selected["holding"], "目前沒有持續加碼確認。")
    lines.append("")
    _lane(lines, "🟢 賣出警示", selected["selling"], "目前沒有新的賣出警示。")
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
