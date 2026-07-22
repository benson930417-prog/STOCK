#!/usr/bin/env python3
"""Render three complete website ETF-action lanes as mobile LINE JPGs."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

from PIL import Image
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.tag_flow_action_cards import (  # noqa: E402
    ACTION_BOARD_CSS,
    ACTION_LANES,
    render_action_lane,
)


ACTION_CACHE = ROOT / "data" / "etf_action_insight.json"
SUMMARY_DIR = ROOT / "data" / "summaries"
OUT_PATHS = {
    "buying": SUMMARY_DIR / "etf_action_buy_latest.jpg",
    "holding": SUMMARY_DIR / "etf_action_hold_latest.jpg",
    "selling": SUMMARY_DIR / "etf_action_sell_latest.jpg",
}


def render_lane_html(payload: dict, lane_key: str) -> str:
    signals = payload.get("signals") or {}
    snapshot = {
        "buying": list(signals.get("buying") or []),
        "holding": list(signals.get("holding") or []),
        "selling": list(signals.get("selling") or []),
    }
    if lane_key not in ACTION_LANES:
        raise ValueError(f"Unknown ETF action lane: {lane_key}")
    lane_html = render_action_lane(snapshot, lane_key)
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <style>
    * {{ box-sizing:border-box; }}
    html {{ background:#0b0e16; font-size:20px; }}
    body {{ margin:0; width:720px; padding:18px; background:#0b0e16; color:#f8fafc;
      font-family:"Noto Sans TC","Microsoft JhengHei","PingFang TC",Arial,sans-serif; }}
    {ACTION_BOARD_CSS}
    .tfv2-lane {{ margin:0; background:rgba(15,23,42,.6); }}
    .tfv2-card {{ background:#101622; box-shadow:0 5px 18px rgba(0,0,0,.12); }}
    .tfv2-lane-title {{ font-size:24px; }}
    .tfv2-count {{ min-width:36px; height:36px; font-size:19px; }}
    .tfv2-lane-note {{ font-size:16px; margin:.18rem 0 .72rem; }}
    .tfv2-card {{ padding:16px 17px; margin:11px 0; }}
    .tfv2-stock {{ font-size:23px; }}
    .tfv2-code {{ font-size:14px; }}
    .tfv2-age {{ font-size:14px; }}
    .tfv2-fields {{ gap:4px; margin-top:9px; }}
    .tfv2-field {{ grid-template-columns:58px minmax(0,1fr); gap:8px;
      font-size:17px; line-height:1.42; }}
    .tfv2-empty {{ font-size:17px; }}
  </style>
</head>
<body>
  {lane_html}
</body>
</html>"""


def _compress(path: Path) -> tuple[int, int]:
    with Image.open(path) as source:
        image = source.convert("RGB")
        size = image.size
        image.save(path, format="JPEG", quality=91, optimize=True, progressive=True)
    return size


def generate(
    cache_path: Path = ACTION_CACHE,
    out_paths: dict[str, Path] | None = None,
) -> list[tuple[Path, int, tuple[int, int]]]:
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    signals = payload.get("signals") or {}
    paths = dict(out_paths or OUT_PATHS)
    if set(paths) != set(ACTION_LANES):
        raise ValueError("ETF action output paths must contain buying/holding/selling")
    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    results: list[tuple[Path, int, tuple[int, int]]] = []
    with sync_playwright() as playwright:
        launch_args: dict[str, object] = {"headless": True}
        browser_path = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
        if browser_path:
            launch_args["executable_path"] = browser_path
        browser = playwright.chromium.launch(**launch_args)
        page = browser.new_page(viewport={"width": 720, "height": 900}, device_scale_factor=1)
        for lane_key in ACTION_LANES:
            expected_cards = len(signals.get(lane_key) or [])
            out_path = paths[lane_key]
            out_path.parent.mkdir(parents=True, exist_ok=True)
            page.set_content(render_lane_html(payload, lane_key), wait_until="load")
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
                    f"ETF action {lane_key} card mismatch: "
                    f"expected {expected_cards}, rendered {rendered_cards}"
                )
            # Element capture grows to the body's computed height, so a lane can
            # never lose its final card to the 900px viewport boundary.
            page.locator("body").screenshot(
                path=str(out_path),
                type="jpeg",
                quality=94,
            )
            size = _compress(out_path)
            results.append((out_path, expected_cards, size))
            print(
                f"Saved {out_path.relative_to(ROOT)} "
                f"({expected_cards} cards, {size[0]}x{size[1]})"
            )
        browser.close()
    return results


if __name__ == "__main__":
    generate()
