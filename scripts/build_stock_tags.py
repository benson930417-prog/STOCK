#!/usr/bin/env python3
"""Build data/stock_tags.json — a stock -> theme-tag map scraped from cmoney forum.

For each stock that appears in the active-ETF histories we fetch its cmoney forum
page (https://www.cmoney.tw/forum/stock/<id>) and extract two tag systems:

  * 類股 / category  — ONE clean sector per stock, e.g. "電子中游-散熱零組件".
                       Stored as {group, leaf} where group is the broad bucket
                       (電子中游 / 電子上游 / 傳產 …) and leaf is the specific
                       theme (散熱零組件, 電源供應器, 銅箔基板…). This is the
                       primary tag used to aggregate ETF flow by theme.
  * 概念股 / concept — MULTIPLE momentum themes per stock, each with cmoney's own
                       日平均漲跌% (theme momentum). Kept as secondary enrichment.

Scraping is per-stock, so cmoney's "查看其他 n 檔股票" pagination on tag *member*
pages never applies here — a stock page always lists its own full tag set.

The map is stable (a stock's classification rarely changes), so this runs monthly
(or whenever a new holding shows up). It is incremental: stocks already present in
the cache are skipped unless --refresh-all is given.

Usage:
    python scripts/build_stock_tags.py            # fill in any missing stocks
    python scripts/build_stock_tags.py --refresh-all
    python scripts/build_stock_tags.py --only 2308,6274
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = DATA / "stock_tags.json"
ACTIVE_HISTORIES = [
    DATA / "etf_00403A_history.json",
    DATA / "etf_00981A_history.json",
    DATA / "etf_00991A_history.json",
]

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

# Broad 類股 buckets cmoney groups leaf categories under (see 類股總覽). The
# category label itself is usually "群組-葉", so we mostly split on "-", but some
# labels have no dash; then we fall back to matching a known group prefix.
KNOWN_GROUPS = [
    "電子上游", "電子中游", "電子下游", "光電業", "半導體",
    "傳產", "金融", "生技", "航運", "鋼鐵", "水泥", "食品", "塑膠",
    "紡織纖維", "電機", "電線電纜", "化學工業", "玻璃陶瓷", "紙業",
    "橡膠", "汽車", "營建", "觀光", "百貨", "貿易百貨", "其他",
]


def load_universe() -> dict[str, str]:
    """Return {stock_id: name} across every date in the active-ETF histories."""
    uni: dict[str, str] = {}
    for f in ACTIVE_HISTORIES:
        if not f.exists():
            continue
        d = json.loads(f.read_text(encoding="utf-8"))
        for day in d.values():
            for h in day.get("holdings", []):
                sid = str(h.get("id", "")).strip()
                if sid:
                    uni[sid] = h.get("name", uni.get(sid, ""))
    return uni


def fetch(stock_id: str, timeout: int = 25) -> str:
    url = f"https://www.cmoney.tw/forum/stock/{stock_id}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def split_group(label: str) -> tuple[str, str]:
    """'電子中游-散熱零組件' -> ('電子中游', '散熱零組件')."""
    label = label.strip()
    if "-" in label:
        grp, leaf = label.split("-", 1)
        return grp.strip(), leaf.strip()
    for g in KNOWN_GROUPS:
        if label.startswith(g) and label != g:
            return g, label[len(g):].strip()
    return "", label


def parse_tags(html: str) -> dict:
    """Extract category (group/leaf) and concept tags with momentum % from a
    stock forum page. Returns {} for the fields it cannot find."""
    out: dict = {"category": None, "group": None, "concepts": []}

    # --- 類股 / category: first /forum/category/C##### anchor with a label ---
    cat = re.search(
        r'/forum/category/(C\d+)"[^>]*>\s*(?:<[^>]+>)?\s*([^<]+?)\s*<', html
    )
    if cat:
        raw = re.sub(r"\s+", " ", cat.group(2)).strip()
        grp, leaf = split_group(raw)
        out["category_id"] = cat.group(1)
        out["category"] = leaf or raw
        out["category_full"] = raw
        out["group"] = grp or None

    # --- 概念股 / concept: the stock's own tag chips (conceptStocks__list-*) ---
    seen = set()
    for m in re.finditer(
        r'/forum/concept/(C\d+)"[^>]*>\s*'
        r'<span class="conceptStocks__list-name"[^>]*>([^<]+)</span>'
        r"(.*?)</a>",
        html,
        re.S,
    ):
        cid, name, tail = m.group(1), m.group(2).strip(), m.group(3)
        if cid in seen or not name:
            continue
        seen.add(cid)
        pm = re.search(r"(-?\d+(?:\.\d+)?)%", tail)
        out["concepts"].append(
            {"id": cid, "name": name, "avg_pct": float(pm.group(1)) if pm else None}
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh-all", action="store_true",
                    help="re-scrape every stock, ignoring the cache")
    ap.add_argument("--only", default="",
                    help="comma-separated stock ids to (re)scrape")
    ap.add_argument("--delay", type=float, default=0.7,
                    help="seconds between requests (be polite)")
    args = ap.parse_args()

    cache: dict = {}
    if OUT.exists():
        cache = json.loads(OUT.read_text(encoding="utf-8"))
    tags: dict = cache.get("tags", {})

    universe = load_universe()
    if args.only:
        targets = [s.strip() for s in args.only.split(",") if s.strip()]
    elif args.refresh_all:
        targets = sorted(universe)
    else:
        targets = [s for s in sorted(universe) if s not in tags]

    print(f"universe={len(universe)} cached={len(tags)} to_fetch={len(targets)}")
    ok = fail = 0
    for i, sid in enumerate(targets, 1):
        name = universe.get(sid, tags.get(sid, {}).get("name", ""))
        try:
            parsed = parse_tags(fetch(sid))
            parsed["name"] = name
            if not parsed.get("category") and not parsed.get("concepts"):
                raise ValueError("no tags parsed")
            tags[sid] = parsed
            ok += 1
            cat = parsed.get("category_full") or parsed.get("category") or "?"
            cons = ",".join(c["name"] for c in parsed["concepts"][:4])
            print(f"  [{i}/{len(targets)}] {sid} {name} -> {cat} | {cons}")
        except Exception as e:  # noqa: BLE001 — keep the crawl alive, log the miss
            fail += 1
            print(f"  [{i}/{len(targets)}] {sid} {name} -> FAIL {type(e).__name__}: {e}",
                  file=sys.stderr)
            tags.setdefault(sid, {"name": name, "category": None, "concepts": []})
        time.sleep(args.delay)

    payload = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": "cmoney.tw/forum/stock",
        "count": len(tags),
        "tags": tags,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT}  ok={ok} fail={fail} total={len(tags)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
