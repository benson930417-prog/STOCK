#!/usr/bin/env python3
"""Render buy/sell top-five V4 images for the on-demand LINE reply."""
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

from src.etf_consensus_v4_cards import (  # noqa: E402
    V4_CSS,
    V4_LANES,
    render_v4_grid,
    render_v4_lane,
)


CACHE = ROOT / "data" / "etf_consensus_v4.json"
SUMMARY_DIR = ROOT / "data" / "summaries"
MANIFEST_PATH = SUMMARY_DIR / "etf_consensus_v4_manifest.json"
LINE_IMAGE_COUNT = 2
LINE_IMAGE_WIDTH = 720
LINE_SAFE_TOP_HEIGHT = 96


def _column(
    *,
    lane_key: str,
    cards: list[dict],
    title: str,
    note: str,
) -> dict:
    return {
        "lane_key": lane_key,
        "cards": cards,
        "title": title,
        "note": note,
    }


def _ranked(cards: list[dict]) -> list[dict]:
    return [
        {**card, "line_rank": index}
        for index, card in enumerate(cards, start=1)
    ]


def build_page_specs(payload: dict) -> list[dict]:
    """Build two decision-summary images; the website retains the full board."""
    signals = payload.get("signals") or {}
    buying = list(signals.get("buying") or [])
    selling = list(signals.get("selling") or [])
    buy_top = _ranked(buying[:5])
    sell_top = _ranked(selling[:5])

    specs = [
        {
            "lane_key": "buying",
            "filename": "etf_consensus_v4_buy_top5_latest.jpg",
            "columns": [
                _column(
                    lane_key="buying",
                    cards=buy_top,
                    title="🔴 買方前 5 名",
                    note="由上往下依共識強度排列 01 → 05；完整名單請看網站",
                )
            ],
        },
        {
            "lane_key": "selling",
            "filename": "etf_consensus_v4_sell_top5_latest.jpg",
            "columns": [
                _column(
                    lane_key="selling",
                    cards=sell_top,
                    title="🟢 賣方前 5 名",
                    note="由上往下依共識強度排列 01 → 05；完整名單請看網站",
                )
            ],
        },
    ]
    for spec in specs:
        spec["cards"] = [
            card for column in spec["columns"] for card in column["cards"]
        ]
        spec["total_count"] = len(spec["cards"])
    all_cards = [
        (spec["lane_key"], str(card.get("stock_id") or ""))
        for spec in specs
        for card in spec["cards"]
    ]
    expected = len(buy_top) + len(sell_top)
    if len(all_cards) != expected or len(set(all_cards)) != expected:
        raise RuntimeError("V4 LINE top-five images contain duplicate cards")
    if len(specs) != LINE_IMAGE_COUNT:
        raise RuntimeError(
            f"V4 LINE summary must contain {LINE_IMAGE_COUNT} images"
        )
    return specs


def _render_column(payload: dict, column: dict) -> str:
    return render_v4_lane(
        payload,
        column["lane_key"],
        cards=column["cards"],
        total_count=len(column["cards"]),
        title_override=column["title"],
        note_override=column["note"],
        show_sections=False,
    )


def _document(body_html: str, *, width: int) -> str:
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <style>
    * {{ box-sizing:border-box; }}
    html {{ background:#0b0e16; font-size:20px; }}
    body {{ margin:0; width:{width}px; padding:0 20px 20px; background:#0b0e16;
      color:#f8fafc; font-family:"Noto Sans TC","Microsoft JhengHei",
      "PingFang TC",Arial,sans-serif; }}
    {V4_CSS}
    .tfv4-safe-top {{ height:{LINE_SAFE_TOP_HEIGHT}px;background:#000;
      margin:0 -20px 20px; }}
    .tfv4-line-grid {{ display:grid;grid-template-columns:1fr;
      gap:18px;align-items:start; }}
    .tfv4-lane {{ margin:0; background-color:rgba(15,23,42,.72); }}
    .tfv4-card {{ padding:16px 17px; margin:11px 0; }}
    .tfv4-title {{ font-size:26px; }}
    .tfv4-count {{ min-width:38px; height:38px; font-size:18px; }}
    .tfv4-note {{ font-size:16px; margin:.2rem 0 .75rem; }}
    .tfv4-stock {{ font-size:25px; }}
    .tfv4-rank {{ min-width:43px;height:31px;font-size:17px;
      margin-right:10px; }}
    .tfv4-code,.tfv4-score,.tfv4-tier {{ font-size:14px; }}
    .tfv4-action {{ font-size:21px; }}
    .tfv4-summary {{ font-size:17px; }}
    .tfv4-core-reason {{ font-size:16px; }}
    .tfv4-meta {{ grid-template-columns:94px minmax(0,1fr);
      font-size:17px; gap:5px 10px; }}
    .tfv4-point {{ font-size:13px; }}
    .tfv4-points {{ display:none; }}
    .tfv4-evidence {{ font-size:16px; }}
    .tfv4-evidence-row small {{ font-size:14px; }}
    .tfv4-chart-note,.tfv4-chart-label {{ font-size:14px; }}
    .tfv4-chart-row {{ grid-template-columns:48px minmax(0,1fr);
      margin:.2rem 0; }}
    .tfv4-chart {{ height:2.5rem; }}
  </style>
</head>
<body><div class="tfv4-safe-top" aria-hidden="true"></div>{body_html}</body>
</html>"""


def render_lane_html(
    payload: dict,
    lane_key: str,
    *,
    cards: list[dict] | None = None,
    total_count: int | None = None,
    page_label: str = "",
    title_override: str = "",
    note_override: str = "",
) -> str:
    """Single-column helper retained for preview and focused renderer tests."""
    lane_html = render_v4_lane(
        payload,
        lane_key,
        cards=cards,
        total_count=total_count,
        page_label=page_label,
        title_override=title_override,
        note_override=note_override,
        show_sections=False,
    )
    return _document(lane_html, width=900)


def render_line_html(payload: dict, spec: dict) -> str:
    columns = "".join(
        _render_column(payload, column) for column in spec["columns"]
    )
    return _document(
        f'<div class="tfv4-line-grid">{columns}</div>',
        width=LINE_IMAGE_WIDTH,
    )


def _compress(path: Path) -> tuple[int, int]:
    with Image.open(path) as source:
        image = source.convert("RGB")
        size = image.size
        image.save(
            path,
            format="JPEG",
            quality=90,
            optimize=True,
            progressive=True,
        )
    return size


def _write_manifest(payload: dict, results: list[dict]) -> None:
    manifest = {
        "schema_version": 3,
        "as_of": payload.get("as_of"),
        "reply_order": "buying_top5_then_selling_top5",
        "layout": "two_mobile_single_column_top5_images_with_iphone_safe_top",
        "selection": {
            "buying": "first_five_by_existing_consensus_score",
            "selling": "first_five_by_existing_consensus_score",
            "watching": "website_only",
        },
        "images": results,
    }
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _remove_stale_outputs(active_filenames: set[str]) -> None:
    legacy_tracked = {
        "etf_consensus_v4_watch_latest.jpg",
        "etf_consensus_v4_buy_latest.jpg",
        "etf_consensus_v4_sell_latest.jpg",
    }
    for path in SUMMARY_DIR.glob("etf_consensus_v4_*_latest.jpg"):
        if path.name not in active_filenames and path.name not in legacy_tracked:
            path.unlink()


def generate(cache_path: Path = CACHE) -> list[tuple[Path, int, tuple[int, int]]]:
    from playwright.sync_api import sync_playwright

    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    specs = build_page_specs(payload)
    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    manifest_images = []
    with sync_playwright() as playwright:
        launch_args: dict[str, object] = {"headless": True}
        browser_path = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
        if browser_path:
            launch_args["executable_path"] = browser_path
        browser = playwright.chromium.launch(**launch_args)
        page = browser.new_page(
            viewport={"width": LINE_IMAGE_WIDTH, "height": 900},
            device_scale_factor=1,
        )
        for spec in specs:
            output = SUMMARY_DIR / spec["filename"]
            expected = spec["total_count"]
            page.set_content(render_line_html(payload, spec), wait_until="load")
            try:
                page.wait_for_function(
                    "document.fonts ? document.fonts.status === 'loaded' : true",
                    timeout=10000,
                )
            except Exception:
                pass
            rendered = page.locator(".tfv4-card").count()
            if rendered != expected:
                browser.close()
                raise RuntimeError(
                    f"V4 {spec['lane_key']} card mismatch: "
                    f"expected {expected}, rendered {rendered}"
                )
            page.locator("body").screenshot(
                path=str(output),
                type="jpeg",
                quality=94,
            )
            size = _compress(output)
            results.append((output, expected, size))
            manifest_images.append(
                {
                    "filename": output.name,
                    "lane": spec["lane_key"],
                    "cards": expected,
                    "width": size[0],
                    "height": size[1],
                }
            )
            print(
                f"Saved {output.relative_to(ROOT)} "
                f"({expected} cards, {size[0]}x{size[1]})"
            )
        browser.close()
    active = {item["filename"] for item in manifest_images}
    _remove_stale_outputs(active)
    _write_manifest(payload, manifest_images)
    print(f"Saved {MANIFEST_PATH.relative_to(ROOT)} ({len(active)} images)")
    return results


def write_html_preview(path: Path, cache_path: Path = CACHE) -> Path:
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    html = f"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8"><style>
* {{ box-sizing:border-box; }}
body {{ margin:0;padding:24px;background:#0b0e16;color:#f8fafc;
  font-family:"Noto Sans TC","Microsoft JhengHei","PingFang TC",Arial,sans-serif; }}
{V4_CSS}
</style></head><body>
<h1>主動 ETF 共識追蹤 V4</h1>
<p>截至 {payload.get("as_of", "")}｜黃燈觀察、紅燈買方共識、綠燈賣方共識</p>
{render_v4_grid(payload)}
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
