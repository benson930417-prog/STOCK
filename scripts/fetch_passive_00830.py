import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
import requests

DATA_DIR = "data"
HISTORY_FILE = os.path.join(DATA_DIR, "passive_00830_history.json")
LOG_FILE = os.path.join(DATA_DIR, "passive_00830_log.json")
URL = "https://www.cathaysite.com.tw/ETF/detail/EBO?tab=etf3"
JSON_API_URL = "https://cwapi.cathaysite.com.tw/api/ETF/GetETFDetailStockList"


def _num(value):
    text = str(value or "").replace(",", "").replace("$", "").replace("%", "").replace("新台幣", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _date_to_key(value):
    match = re.search(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", str(value or ""))
    if not match:
        raise ValueError(f"Could not parse 00830 date from {value!r}")
    return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"


def _load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def _extract_page_meta(html_text):
    # Strip HTML tags to make it plain text for the regex
    text = re.sub(r'<[^>]+>', ' ', html_text)
    if "Access Denied" in text or "edgesuite.net" in text:
        raise PermissionError("Cathay blocked this host with Access Denied")
    date_key = _date_to_key(text)

    def value_after(label):
        match = re.search(label + r".{0,40}?\$?\s*([0-9,]+(?:\.\d+)?)", text)
        return _num(match.group(1)) if match else None

    return date_key, {
        "fund_size": value_after("基金資產總淨值"),
        "nav": value_after("基金每單位淨值") or value_after("淨值"),
        "outstanding_units": int(value_after("基金在外流通單位數") or 0) or None,
        "closing_price": value_after("收盤價"),
        "source_url": URL,
    }


def fetch_and_update_00830():
    now_utc = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    history = _load_json(HISTORY_FILE, {})
    previous_log = _load_json(LOG_FILE, {})

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    }

    # Fetch HTML meta directly to avoid Playwright Akamai block
    r_html = requests.get(URL, headers=headers, timeout=15)
    r_html.raise_for_status()
    date_key, meta = _extract_page_meta(r_html.text)

    # Fetch Holdings from JSON API
    params = {
        "FundCode": "BO",
        "SearchDate": date_key
    }
    r_json = requests.get(JSON_API_URL, headers=headers, params=params, timeout=15)
    r_json.raise_for_status()
    api_data = r_json.json()
    
    if not api_data.get("success") or not api_data.get("result"):
        raise ValueError(f"Failed to fetch 00830 holdings from API: {api_data.get('returnMessage')}")

    holdings = []
    for item in api_data["result"]:
        code = str(item.get("stockCode") or "").strip().upper()
        if not code:
            continue
        # Ensure code has .US extension as in original Excel parsing logic
        if "." not in code and not code.endswith("US"):
            code = f"{code}.US"
        
        weight = float(item.get("weights") or 0)
        if weight <= 0 or weight > 100:
            continue
            
        shares_str = str(item.get("volumn") or "").replace(",", "")
        shares = int(shares_str) if shares_str.isdigit() else 0

        holdings.append({
            "id": code,
            "name": str(item.get("stockName") or "").strip(),
            "weight_pct": weight,
            "shares": shares,
        })

    if len(holdings) < 10:
        raise ValueError(f"Only parsed {len(holdings)} 00830 holdings from API")

    payload = {
        "date": date_key,
        "meta": meta,
        "holdings": holdings,
    }
    previous = history.get(date_key)
    changed = previous != payload
    history[date_key] = payload
    _write_json(HISTORY_FILE, dict(sorted(history.items())))
    _write_json(
        LOG_FILE,
        {
            "last_checked_utc": now_utc,
            "last_updated_utc": now_utc if changed else previous_log.get("last_updated_utc"),
            "latest_date": date_key,
            "status": "NEW DATA FOUND" if changed else "NO CHANGE",
            "source": URL,
            "holdings_count": len(holdings),
        },
    )

    if changed:
        print(f"Successfully updated 00830 holdings for {date_key}. Total stocks: {len(holdings)}")
    else:
        print(f"No holding changes detected for 00830. Latest stored date remains {date_key}.")


if __name__ == "__main__":
    fetch_and_update_00830()
