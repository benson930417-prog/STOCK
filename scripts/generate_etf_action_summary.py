#!/usr/bin/env python3
"""Render the complete website ETF-action board as a cached LINE JPG."""
from __future__ import annotations

import html
import json
import os
from pathlib import Path
import sys

from PIL import Image
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.tag_flow_action_cards import ACTION_BOARD_CSS, render_action_grid  # noqa: E402


ACTION_CACHE = ROOT / "data" / "etf_action_insight.json"
OUT = ROOT / "data" / "summaries" / "etf_action_latest.jpg"


def render_html(payload: dict) -> str:
    signals = payload.get("signals") or {}
    snapshot = {
        "buying": list(signals.get("buying") or []),
        "holding": list(signals.get("holding") or []),
        "selling": list(signals.get("selling") or []),
    }
    as_of = html.escape(str(payload.get("as_of") or "未提供"))
    buy_count = len(snapshot["buying"])
    hold_count = len(snapshot["holding"])
    sell_count = len(snapshot["selling"])
    board = render_action_grid(snapshot)
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <style>
    * {{ box-sizing:border-box; }}
    html {{ background:#0b0e16; font-size:18px; }}
    body {{ margin:0; width:1600px; padding:32px; background:#0b0e16; color:#f8fafc;
      font-family:"Noto Sans TC","Microsoft JhengHei","PingFang TC",Arial,sans-serif; }}
    {ACTION_BOARD_CSS}
    .card-head {{ display:flex; justify-content:space-between; align-items:flex-end;
      gap:24px; margin:0 0 20px; }}
    .eyebrow {{ color:#64748b; font-size:14px; font-weight:900; letter-spacing:.14em; }}
    h1 {{ margin:5px 0 0; font-size:31px; line-height:1.2; letter-spacing:-.02em; }}
    .as-of {{ color:#94a3b8; font-size:17px; font-weight:800; white-space:nowrap; }}
    .rules {{ display:grid; grid-template-columns:repeat(3,1fr); gap:12px; margin-bottom:18px; }}
    .rule {{ border:1px solid rgba(148,163,184,.22); border-radius:12px;
      padding:12px 14px; background:rgba(30,41,59,.28); color:#cbd5e1;
      font-size:14px; line-height:1.42; }}
    .rule b {{ display:block; color:#f8fafc; font-size:15px; margin-bottom:2px; }}
    .summary {{ border:1px solid rgba(148,163,184,.22); border-radius:12px;
      padding:12px 16px; background:rgba(30,41,59,.38); color:#cbd5e1;
      font-size:15px; margin-bottom:18px; }}
    .summary b {{ color:#f8fafc; }}
    .tfv2-grid {{ margin:0; align-items:start; }}
    .tfv2-lane {{ height:100%; background:rgba(15,23,42,.6); }}
    .tfv2-card {{ background:#101622; box-shadow:0 5px 18px rgba(0,0,0,.12); }}
    .tfv2-lane-title {{ font-size:19px; }}
    .tfv2-lane-note {{ font-size:13px; }}
    .tfv2-stock {{ font-size:18px; }}
    .tfv2-code {{ font-size:12px; }}
    .tfv2-age {{ font-size:12px; }}
    .tfv2-field {{ grid-template-columns:48px minmax(0,1fr); font-size:14px; }}
    .footer {{ margin-top:16px; color:#64748b; font-size:13px; line-height:1.5; }}
  </style>
</head>
<body>
  <header class="card-head">
    <div><div class="eyebrow">MASTER WU · ACTIVE ETF ACTION RADAR</div><h1>主動 ETF 買／抱／賣雷達</h1></div>
    <div class="as-of">資料截至 {as_of}</div>
  </header>
  <div class="rules">
    <div class="rule"><b>一般訊號：至少 2/3 同向</b>普通加減碼少於兩檔 ETF 不顯示。</div>
    <div class="rule"><b>1/3 只留窄例外</b>建倉／出清，或反轉連續兩日。</div>
    <div class="rule"><b>續抱：至少 2 檔參與</b>近 10 日反覆加碼，而且最新仍在買。</div>
  </div>
  <div class="summary"><b>本次完整看板：</b>買進 {buy_count} 檔・續抱 {hold_count} 檔・賣出 {sell_count} 檔</div>
  {board}
  <div class="footer">買進／賣出只保留本交易日與前一共同交易日；續抱依最新資料每日重算。紅色＝買進，橘色＝續抱，綠色＝賣出。</div>
</body>
</html>"""


def _compress(path: Path) -> tuple[int, int]:
    with Image.open(path) as source:
        image = source.convert("RGB")
        size = image.size
        image.save(path, format="JPEG", quality=91, optimize=True, progressive=True)
    return size


def generate(cache_path: Path = ACTION_CACHE, out_path: Path = OUT) -> tuple[Path, int, tuple[int, int]]:
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    signals = payload.get("signals") or {}
    expected_cards = sum(
        len(signals.get(key) or []) for key in ("buying", "holding", "selling")
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        launch_args: dict[str, object] = {"headless": True}
        browser_path = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
        if browser_path:
            launch_args["executable_path"] = browser_path
        browser = playwright.chromium.launch(**launch_args)
        page = browser.new_page(viewport={"width": 1600, "height": 900}, device_scale_factor=1)
        page.set_content(render_html(payload), wait_until="load")
        try:
            page.wait_for_function(
                "document.fonts ? document.fonts.status === 'loaded' : true",
                timeout=10000,
            )
        except Exception:
            pass
        rendered_cards = page.locator(".tfv2-card").count()
        if rendered_cards != expected_cards:
            browser.close()
            raise RuntimeError(
                f"ETF action card mismatch: expected {expected_cards}, rendered {rendered_cards}"
            )
        # Element capture grows to the body's computed height. It never inherits
        # the viewport's 900px height, so the last card cannot be cropped away.
        page.locator("body").screenshot(
            path=str(out_path),
            type="jpeg",
            quality=94,
        )
        browser.close()
    size = _compress(out_path)
    print(
        f"Saved {out_path.relative_to(ROOT)} "
        f"({expected_cards} cards, {size[0]}x{size[1]})"
    )
    return out_path, expected_cards, size


if __name__ == "__main__":
    generate()
