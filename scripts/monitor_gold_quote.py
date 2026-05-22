import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

import requests
from playwright.sync_api import sync_playwright


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
QUOTE_CACHE_DIR = DATA_DIR / "quote_cache"
GOLD_CACHE_PATH = QUOTE_CACHE_DIR / "gold_quote.json"

TRADINGVIEW_CFD_SCAN_URL = "https://scanner.tradingview.com/cfd/scan"
GOLD_SYMBOL = "TVC:GOLD"
GOLD_URL = "https://www.tradingview.com/symbols/GOLD/"
HEADERS = {"User-Agent": "Mozilla/5.0"}
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36"
)
PERFORMANCE_LABELS = {
    "1 day": "1d",
    "5 days": "5d",
    "1 month": "1m",
    "6 months": "6m",
    "Year to date": "ytd",
    "1 year": "1y",
    "5 years": "5y",
    "10 years": "10y",
    "All time": "all",
}


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _num(value):
    text = str(value or "")
    text = text.replace("−", "-").replace("\u202a", "").replace("\u202c", "")
    text = text.replace("\u202f", "").replace(",", "").replace("%", "").strip()
    text = re.sub(r"[^\d.+\-kKmM]", "", text)
    if not text:
        return None
    multiplier = 1.0
    if text[-1:].lower() == "k":
        multiplier = 1_000.0
        text = text[:-1]
    elif text[-1:].lower() == "m":
        multiplier = 1_000_000.0
        text = text[:-1]
    try:
        return float(text) * multiplier
    except ValueError:
        return None


def _parse_performance_from_text(text):
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    performance = {}
    for idx, line in enumerate(lines[:-1]):
        key = PERFORMANCE_LABELS.get(line)
        if not key:
            continue
        value = _num(lines[idx + 1])
        if value is not None:
            performance[key] = value
    return performance


def _parse_gold_page_text(text):
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    price = change_abs = change_pct = None
    currency = "USD"
    as_of = None

    for idx, line in enumerate(lines):
        if line == "GOLD" and idx + 3 < len(lines):
            maybe_price = _num(lines[idx + 1])
            if maybe_price and lines[idx + 2].upper() == "USD":
                price = maybe_price
                currency = lines[idx + 2].upper()
                change_abs = _num(lines[idx + 3])
                if idx + 4 < len(lines):
                    change_pct = _num(lines[idx + 4])
                if idx + 5 < len(lines) and lines[idx + 5].startswith("As of"):
                    as_of = lines[idx + 5]

    if price is None:
        match = re.search(
            r"GOLD\s+([\d,]+\.\d+)\s+USD\s+([+\-−]?\d+(?:\.\d+)?)\s+([+\-−]?\d+(?:\.\d+)?)%",
            str(text or ""),
        )
        if match:
            price = _num(match.group(1))
            change_abs = _num(match.group(2))
            change_pct = _num(match.group(3))

    if price is None:
        raise RuntimeError("TradingView GOLD page text did not include a parsable price")

    return {
        "symbol": GOLD_SYMBOL,
        "name": "GOLD",
        "price": price,
        "change_pct": change_pct,
        "change_abs": change_abs,
        "description": "GOLD (US$/OZ)",
        "currency": currency,
        "update_mode": "page",
        "source": "tradingview_page_dom",
        "source_url": GOLD_URL,
        "as_of_text": as_of,
        "performance": _parse_performance_from_text(text),
        "quote_time_utc": utc_now_iso(),
    }


def fetch_gold_quote_from_page(page):
    page.goto(GOLD_URL, wait_until="networkidle", timeout=60000)
    text = page.locator("body").inner_text(timeout=10000)
    return _parse_gold_page_text(text)


def fetch_gold_quote(timeout=10):
    payload = {
        "symbols": {"tickers": [GOLD_SYMBOL], "query": {"types": []}},
        "columns": [
            "name",
            "close",
            "change",
            "change_abs",
            "description",
            "currency",
            "update_mode",
        ],
    }
    res = requests.post(TRADINGVIEW_CFD_SCAN_URL, json=payload, headers=HEADERS, timeout=timeout)
    res.raise_for_status()
    rows = res.json().get("data") or []
    if not rows:
        raise RuntimeError("TradingView returned no GOLD rows")

    values = rows[0].get("d") or []
    price = values[1] if len(values) > 1 else None
    if price is None:
        raise RuntimeError("TradingView GOLD row is missing close")

    return {
        "symbol": GOLD_SYMBOL,
        "name": values[0] if len(values) > 0 else "GOLD",
        "price": float(price),
        "change_pct": float(values[2]) if len(values) > 2 and values[2] is not None else None,
        "change_abs": float(values[3]) if len(values) > 3 and values[3] is not None else None,
        "description": values[4] if len(values) > 4 else "GOLD (US$/OZ)",
        "currency": values[5] if len(values) > 5 else "USD",
        "update_mode": values[6] if len(values) > 6 else None,
        "source": "tradingview_cfd_scan",
        "source_url": GOLD_URL,
        "quote_time_utc": utc_now_iso(),
    }


def atomic_write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        json.dump(payload, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
        tmp_path = Path(tmp.name)
    tmp_path.chmod(0o644)
    tmp_path.replace(path)


def refresh_once(page=None):
    quote = fetch_gold_quote_from_page(page) if page is not None else fetch_gold_quote()
    atomic_write_json(GOLD_CACHE_PATH, quote)
    print(
        f"{quote['quote_time_utc']} updated GOLD quote: "
        f"{quote['price']:.2f} {quote.get('currency', '')} "
        f"({quote.get('change_pct'):+.2f}%)",
        flush=True,
    )
    return quote


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    if args.once:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=USER_AGENT, timezone_id="Asia/Taipei")
            try:
                refresh_once(page=page)
            finally:
                browser.close()
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=USER_AGENT, timezone_id="Asia/Taipei")
        while True:
            try:
                refresh_once(page=page)
            except Exception as exc:
                print(f"{utc_now_iso()} GOLD page update failed: {exc}", flush=True)
                try:
                    refresh_once()
                except Exception as fallback_exc:
                    print(f"{utc_now_iso()} GOLD scanner fallback failed: {fallback_exc}", flush=True)
            time.sleep(max(10, args.interval))


if __name__ == "__main__":
    main()
