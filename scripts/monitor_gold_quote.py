import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

import requests


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
QUOTE_CACHE_DIR = DATA_DIR / "quote_cache"
GOLD_CACHE_PATH = QUOTE_CACHE_DIR / "gold_quote.json"

TRADINGVIEW_CFD_SCAN_URL = "https://scanner.tradingview.com/cfd/scan"
GOLD_SYMBOL = "TVC:GOLD"
HEADERS = {"User-Agent": "Mozilla/5.0"}


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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
        "source_url": "https://www.tradingview.com/symbols/GOLD/",
        "quote_time_utc": utc_now_iso(),
    }


def atomic_write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        json.dump(payload, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def refresh_once():
    quote = fetch_gold_quote()
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

    while True:
        try:
            refresh_once()
        except Exception as exc:
            print(f"{utc_now_iso()} GOLD quote update failed: {exc}", flush=True)
        if args.once:
            break
        time.sleep(max(10, args.interval))


if __name__ == "__main__":
    main()
