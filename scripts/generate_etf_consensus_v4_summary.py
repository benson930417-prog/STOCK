#!/usr/bin/env python3
"""Render the V4 board into at most five mobile-first cached LINE JPGs."""
from __future__ import annotations

import argparse
import json
import math
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
MAX_LINE_IMAGES = 5
REPLY_LANE_ORDER = ("buying", "selling", "watching")
LANE_SLUG = {
    "watching": "watch",
    "buying": "buy",
    "selling": "sell",
}


def allocate_pages(payload: dict, max_pages: int = MAX_LINE_IMAGES) -> dict[str, int]:
    """Keep every lane, then spend spare LINE objects on the longest lanes."""
    if max_pages < len(V4_LANES):
        raise ValueError("V4 pagination needs at least one image per lane")
    counts = {
        lane_key: len((payload.get("signals") or {}).get(lane_key) or [])
        for lane_key in V4_LANES
    }
    pages = {lane_key: 1 for lane_key in V4_LANES}
    for _ in range(max_pages - len(V4_LANES)):
        candidates = [
            lane_key
            for lane_key, count in counts.items()
            if count > pages[lane_key]
        ]
        if not candidates:
            break
        lane_key = max(
            candidates,
            key=lambda key: (
                counts[key] / pages[key],
                counts[key],
                -REPLY_LANE_ORDER.index(key),
            ),
        )
        pages[lane_key] += 1
    return pages


def partition_cards(cards: list[dict], page_count: int) -> list[list[dict]]:
    """Split in stable score order while keeping page lengths nearly equal."""
    if page_count < 1:
        raise ValueError("page_count must be positive")
    if not cards:
        return [[]]
    page_count = min(page_count, len(cards))
    chunk_size = math.ceil(len(cards) / page_count)
    return [
        cards[index : index + chunk_size]
        for index in range(0, len(cards), chunk_size)
    ]


def build_page_specs(payload: dict) -> list[dict]:
    """Return the exact ordered image reply contract for the current payload."""
    allocation = allocate_pages(payload)
    specs = []
    signals = payload.get("signals") or {}
    for lane_key in REPLY_LANE_ORDER:
        cards = list(signals.get(lane_key) or [])
        chunks = partition_cards(cards, allocation[lane_key])
        for page_index, chunk in enumerate(chunks, start=1):
            page_count = len(chunks)
            filename = (
                f"etf_consensus_v4_{LANE_SLUG[lane_key]}"
                f"_p{page_index}_latest.jpg"
            )
            specs.append(
                {
                    "lane_key": lane_key,
                    "cards": chunk,
                    "total_count": len(cards),
                    "page_index": page_index,
                    "page_count": page_count,
                    "page_label": (
                        f"{page_index}/{page_count}" if page_count > 1 else ""
                    ),
                    "filename": filename,
                }
            )
    if not 1 <= len(specs) <= MAX_LINE_IMAGES:
        raise RuntimeError(
            f"V4 LINE reply needs 1..{MAX_LINE_IMAGES} images, got {len(specs)}"
        )
    return specs


def render_lane_html(
    payload: dict,
    lane_key: str,
    *,
    cards: list[dict] | None = None,
    total_count: int | None = None,
    page_label: str = "",
) -> str:
    if lane_key not in V4_LANES:
        raise ValueError(f"Unknown V4 lane: {lane_key}")
    lane_html = render_v4_lane(
        payload,
        lane_key,
        cards=cards,
        total_count=total_count,
        page_label=page_label,
    )
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
    {V4_CSS}
    .tfv4-lane {{ margin:0; background-color:rgba(15,23,42,.72); }}
    .tfv4-card {{ padding:16px 17px; margin:11px 0; }}
    .tfv4-title {{ font-size:24px; }}
    .tfv4-count {{ min-width:36px; height:36px; font-size:17px; }}
    .tfv4-note {{ font-size:15px; }}
    .tfv4-section-label {{ font-size:14px; margin-top:14px; }}
    .tfv4-stock {{ font-size:23px; }}
    .tfv4-code,.tfv4-score,.tfv4-tier {{ font-size:13px; }}
    .tfv4-action {{ font-size:19px; }}
    .tfv4-summary {{ font-size:16px; }}
    .tfv4-core-reason {{ font-size:14px; }}
    .tfv4-meta {{ grid-template-columns:94px minmax(0,1fr);
      font-size:16px; gap:4px 10px; }}
    .tfv4-point {{ font-size:13px; }}
    .tfv4-evidence {{ font-size:15px; }}
    .tfv4-chart-note,.tfv4-chart-label {{ font-size:13px; }}
    .tfv4-chart-row {{ grid-template-columns:42px minmax(0,1fr); }}
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
            quality=90,
            optimize=True,
            progressive=True,
        )
    return size


def _write_manifest(payload: dict, results: list[dict]) -> None:
    manifest = {
        "schema_version": 1,
        "as_of": payload.get("as_of"),
        "reply_order": "buying_then_selling_then_watching",
        "max_line_images": MAX_LINE_IMAGES,
        "images": results,
    }
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _remove_stale_pages(active_filenames: set[str]) -> None:
    for path in SUMMARY_DIR.glob("etf_consensus_v4_*_p*_latest.jpg"):
        if path.name not in active_filenames:
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
            viewport={"width": 720, "height": 900},
            device_scale_factor=1.25,
        )
        for spec in specs:
            output = SUMMARY_DIR / spec["filename"]
            expected = len(spec["cards"])
            page.set_content(
                render_lane_html(
                    payload,
                    spec["lane_key"],
                    cards=spec["cards"],
                    total_count=spec["total_count"],
                    page_label=spec["page_label"],
                ),
                wait_until="load",
            )
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
                    f"V4 {spec['lane_key']} page {spec['page_index']} "
                    f"card mismatch: expected {expected}, rendered {rendered}"
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
                    "page": spec["page_index"],
                    "pages": spec["page_count"],
                    "cards": expected,
                    "lane_total": spec["total_count"],
                    "width": size[0],
                    "height": size[1],
                }
            )
            print(
                f"Saved {output.relative_to(ROOT)} "
                f"({expected}/{spec['total_count']} cards, {size[0]}x{size[1]})"
            )
        browser.close()
    active = {item["filename"] for item in manifest_images}
    _remove_stale_pages(active)
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
