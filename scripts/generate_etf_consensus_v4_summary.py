#!/usr/bin/env python3
"""Render three wide, two-column V4 images for the on-demand LINE reply."""
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
LINE_IMAGE_COUNT = 3


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


def _balanced_columns(
    *,
    lane_key: str,
    cards: list[dict],
    title: str,
    note: str,
) -> list[dict]:
    cut = (len(cards) + 1) // 2
    halves = (cards[:cut], cards[cut:])
    columns = []
    start = 1
    for half in halves:
        end = start + len(half) - 1
        range_label = f"{start}–{end}" if half else "無"
        columns.append(
            _column(
                lane_key=lane_key,
                cards=half,
                title=f"{title}｜順序 {range_label}",
                note=note,
            )
        )
        start = end + 1
    return columns


def build_page_specs(payload: dict) -> list[dict]:
    """Build exactly three images; every current card appears exactly once."""
    signals = payload.get("signals") or {}
    buying = list(signals.get("buying") or [])
    selling = list(signals.get("selling") or [])
    watching = list(signals.get("watching") or [])

    specs = [
        {
            "lane_key": "buying",
            "filename": "etf_consensus_v4_buy_wide_latest.jpg",
            "columns": _balanced_columns(
                lane_key="buying",
                cards=buying,
                title="🔴 買方共識",
                note="依共識強度排序；核心決策徽章仍標示真正優先名單",
            ),
        },
        {
            "lane_key": "selling",
            "filename": "etf_consensus_v4_sell_wide_latest.jpg",
            "columns": _balanced_columns(
                lane_key="selling",
                cards=selling,
                title="🟢 賣方共識",
                note="依共識強度排序；核心決策徽章標示優先警示",
            ),
        },
        {
            "lane_key": "watching",
            "filename": "etf_consensus_v4_watch_wide_latest.jpg",
            "columns": _balanced_columns(
                lane_key="watching",
                cards=watching,
                title="🟡 單一 ETF 觀察",
                note="依觀察成熟度排序；分數再高也仍不等於兩檔 ETF 共識",
            ),
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
    expected = len(buying) + len(selling) + len(watching)
    if len(all_cards) != expected or len(set(all_cards)) != expected:
        raise RuntimeError("V4 wide images must contain every card exactly once")
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
    body {{ margin:0; width:{width}px; padding:20px; background:#0b0e16;
      color:#f8fafc; font-family:"Noto Sans TC","Microsoft JhengHei",
      "PingFang TC",Arial,sans-serif; }}
    {V4_CSS}
    .tfv4-wide-grid {{ display:grid;grid-template-columns:1fr 1fr;
      gap:18px;align-items:start; }}
    .tfv4-lane {{ margin:0; background-color:rgba(15,23,42,.72); }}
    .tfv4-card {{ padding:13px 15px; margin:7px 0; }}
    .tfv4-title {{ font-size:24px; }}
    .tfv4-count {{ min-width:36px; height:36px; font-size:17px; }}
    .tfv4-note {{ font-size:15px; min-height:43px; }}
    .tfv4-stock {{ font-size:23px; }}
    .tfv4-code,.tfv4-score,.tfv4-tier {{ font-size:13px; }}
    .tfv4-action {{ font-size:19px; }}
    .tfv4-summary {{ font-size:16px; }}
    .tfv4-core-reason {{ font-size:14px; }}
    .tfv4-meta {{ grid-template-columns:94px minmax(0,1fr);
      font-size:16px; gap:4px 10px; }}
    .tfv4-point {{ font-size:13px; }}
    .tfv4-points {{ display:none; }}
    .tfv4-evidence {{ font-size:15px; }}
    .tfv4-chart-note,.tfv4-chart-label {{ font-size:13px; }}
    .tfv4-chart-row {{ grid-template-columns:42px minmax(0,1fr); }}
    .tfv4-chart {{ height:1.55rem; }}
  </style>
</head>
<body>{body_html}</body>
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


def render_wide_html(payload: dict, spec: dict) -> str:
    columns = "".join(
        _render_column(payload, column) for column in spec["columns"]
    )
    return _document(
        f'<div class="tfv4-wide-grid">{columns}</div>',
        width=1800,
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
        "schema_version": 2,
        "as_of": payload.get("as_of"),
        "reply_order": "buying_then_selling_then_watching",
        "layout": "three_wide_two_column_images",
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
            viewport={"width": 1800, "height": 900},
            device_scale_factor=1,
        )
        for spec in specs:
            output = SUMMARY_DIR / spec["filename"]
            expected = spec["total_count"]
            page.set_content(render_wide_html(payload, spec), wait_until="load")
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
