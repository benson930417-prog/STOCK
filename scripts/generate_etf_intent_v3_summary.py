#!/usr/bin/env python3
"""Render the two complete V3 intent lanes as mobile LINE JPGs."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.etf_intent_v3_cards import (  # noqa: E402
    INTENT_CSS,
    INTENT_LANES,
    render_intent_grid,
    render_intent_lane,
)


CACHE = ROOT / "data" / "etf_intent_v3.json"
SUMMARY_DIR = ROOT / "data" / "summaries"
OUT_PATHS = {
    "buying": SUMMARY_DIR / "etf_intent_v3_buy_latest.jpg",
    "selling": SUMMARY_DIR / "etf_intent_v3_sell_latest.jpg",
}


def render_lane_html(payload: dict, lane_key: str) -> str:
    if lane_key not in INTENT_LANES:
        raise ValueError(f"Unknown V3 lane: {lane_key}")
    lane_html = render_intent_lane(payload, lane_key)
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <style>
    * {{ box-sizing:border-box; }}
    html {{ background:#0b0e16; font-size:20px; }}
    body {{ margin:0; width:720px; padding:18px; background:#0b0e16;
      color:#f8fafc; font-family:"Noto Sans TC","Microsoft JhengHei",
      "PingFang TC",Arial,sans-serif; }}
    {INTENT_CSS}
    .tfv3-lane {{ margin:0; background:rgba(15,23,42,.6); }}
    .tfv3-card {{ background:#101622; }}
    .tfv3-lane-title {{ font-size:24px; }}
    .tfv3-count {{ min-width:36px; height:36px; font-size:19px; }}
    .tfv3-lane-note {{ font-size:16px; }}
    .tfv3-card {{ padding:16px 17px; margin:11px 0; }}
    .tfv3-stock {{ font-size:23px; }}
    .tfv3-code,.tfv3-time {{ font-size:14px; }}
    .tfv3-signal {{ font-size:19px; }}
    .tfv3-reason {{ font-size:16px; }}
    .tfv3-meta {{ grid-template-columns:94px minmax(0,1fr);
      font-size:16px; gap:4px 10px; }}
    .tfv3-evidence {{ font-size:15px; }}
  </style>
</head>
<body>{lane_html}</body>
</html>"""


def _compress(path: Path) -> tuple[int, int]:
    with Image.open(path) as source:
        image = source.convert("RGB")
        size = image.size
        image.save(
            path,
            format="JPEG",
            quality=91,
            optimize=True,
            progressive=True,
        )
    return size


def generate(
    cache_path: Path = CACHE,
    out_paths: dict[str, Path] | None = None,
) -> list[tuple[Path, int, tuple[int, int]]]:
    from playwright.sync_api import sync_playwright

    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    paths = dict(out_paths or OUT_PATHS)
    if set(paths) != set(INTENT_LANES):
        raise ValueError("V3 output paths must contain buying/selling")
    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    with sync_playwright() as playwright:
        launch_args: dict[str, object] = {"headless": True}
        browser_path = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
        if browser_path:
            launch_args["executable_path"] = browser_path
        browser = playwright.chromium.launch(**launch_args)
        page = browser.new_page(
            viewport={"width": 720, "height": 900},
            device_scale_factor=1,
        )
        for lane_key in INTENT_LANES:
            expected = len((payload.get("signals") or {}).get(lane_key) or [])
            output = paths[lane_key]
            output.parent.mkdir(parents=True, exist_ok=True)
            page.set_content(render_lane_html(payload, lane_key), wait_until="load")
            try:
                page.wait_for_function(
                    "document.fonts ? document.fonts.status === 'loaded' : true",
                    timeout=10000,
                )
            except Exception:
                pass
            rendered = page.locator(".tfv3-card").count()
            if rendered != expected:
                browser.close()
                raise RuntimeError(
                    f"V3 {lane_key} card mismatch: expected {expected}, "
                    f"rendered {rendered}"
                )
            page.locator("body").screenshot(
                path=str(output),
                type="jpeg",
                quality=94,
            )
            size = _compress(output)
            results.append((output, expected, size))
            print(
                f"Saved {output.relative_to(ROOT)} "
                f"({expected} cards, {size[0]}x{size[1]})"
            )
        browser.close()
    return results


def write_html_preview(path: Path, cache_path: Path = CACHE) -> Path:
    """Write a dependency-free two-lane preview for local visual QA."""
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    html = f"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8"><style>
* {{ box-sizing:border-box; }}
body {{ margin:0; padding:24px; background:#0b0e16; color:#f8fafc;
  font-family:"Noto Sans TC","Microsoft JhengHei","PingFang TC",Arial,sans-serif; }}
{INTENT_CSS}
</style></head><body>
<h1>主動 ETF 意圖轉折</h1>
<p>截至 {payload.get("as_of", "")}｜只顯示新的買方／賣方意圖</p>
{render_intent_grid(payload)}
</body></html>"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--html-preview", type=Path)
    args = parser.parse_args()
    if args.html_preview:
        output = write_html_preview(args.html_preview)
        print(f"Saved {output}")
    else:
        generate()
