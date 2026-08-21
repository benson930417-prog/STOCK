import json
import os
import re
import urllib3
from datetime import datetime, timezone, timedelta
import requests
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DATA_DIR = "data"
HISTORY_FILE = os.path.join(DATA_DIR, "passive_009805_history.json")
LOG_FILE = os.path.join(DATA_DIR, "passive_009805_log.json")
URL = "https://www.tsit.com.tw/ETF/Home/ETFSeriesDetail/009805"

def _num(value):
    text = str(value or "").replace(",", "").replace("$", "").replace("%", "").replace("新台幣", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None

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

def _holdings_signature(holdings):
    return [
        (
            str(item.get("id") or "").strip().upper(),
            str(item.get("name") or "").strip(),
            item.get("weight_pct"),
            item.get("shares"),
        )
        for item in holdings or []
    ]

def fetch_and_update_009805():
    now_utc = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    history = _load_json(HISTORY_FILE, {})
    previous_log = _load_json(LOG_FILE, {})

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    }

    # Fetch the issuer page for the disclosure date and NAV.  Market prices
    # are owned exclusively by canonical market.db daily_bars.
    r_main = requests.get("https://www.tsit.com.tw/ETF/", headers=headers, verify=False, timeout=15)
    r_main.raise_for_status()
    soup_main = BeautifulSoup(r_main.text, 'html.parser')

    # 1. Extract Date and NAV from the carousel
    # Look for the block containing "009805"
    date_key = None
    nav = None

    carousel_items = soup_main.find_all("div", class_="index_carouse")
    for item in carousel_items:
        title = item.find("h3")
        if title and "009805" in title.text:
            date_tag = item.find("h6")
            if date_tag:
                date_key = date_tag.text.strip()
            
            table = item.find("table")
            if table:
                rows = table.find_all("tr")
                for row in rows:
                    cols = row.find_all("td")
                    if len(cols) >= 2:
                        label1 = cols[0].text.strip()
                        val1 = cols[1].text.strip()
                        if label1 == "淨值":
                            nav = _num(val1)
            break

    if not date_key or nav is None:
        raise ValueError("Failed to parse date or NAV from the carousel.")

    meta = {
        "fund_size": None, # Optional as per user
        "nav": nav,
        "outstanding_units": None, # Optional as per user
        "source_url": URL,
    }

    # 2. Fetch Holdings from "投資組合" section in detail page
    r_detail = requests.get(URL, headers=headers, verify=False, timeout=15)
    r_detail.raise_for_status()
    soup_detail = BeautifulSoup(r_detail.text, 'html.parser')

    holdings = []
    # Find the table where headers are 代號, 名稱, 股數, 持股權重
    tables = soup_detail.find_all("table", class_="table-striped")
    for table in tables:
        thead = table.find("thead")
        if not thead:
            continue
        headers_text = [th.text.strip() for th in thead.find_all("th")]
        if "代號" in headers_text and "持股權重" in headers_text:
            tbody = table.find("tbody")
            if not tbody:
                continue
            for tr in tbody.find_all("tr"):
                tds = tr.find_all("td")
                if len(tds) >= 4:
                    code = tds[0].text.strip().upper()
                    name = tds[1].text.strip()
                    shares = _num(tds[2].text)
                    weight = _num(tds[3].text)
                    
                    if not code or weight is None or weight <= 0:
                        continue
                        
                    holdings.append({
                        "id": code,
                        "name": name,
                        "weight_pct": weight,
                        "shares": int(shares) if shares else 0,
                    })
            break

    if len(holdings) < 10:
        raise ValueError(f"Only parsed {len(holdings)} 009805 holdings from page.")

    latest_existing_date = max(history) if history else None
    if latest_existing_date and date_key > latest_existing_date:
        latest_payload = history.get(latest_existing_date) or {}
        if _holdings_signature(holdings) == _holdings_signature(latest_payload.get("holdings")):
            _write_json(
                LOG_FILE,
                {
                    "last_checked_utc": now_utc,
                    "last_updated_utc": previous_log.get("last_updated_utc"),
                    "latest_date": latest_existing_date,
                    "provider_date": date_key,
                    "status": "NO CHANGE",
                    "source": URL,
                    "holdings_count": len(holdings),
                    "message": f"Ignored provider date {date_key}: holdings table still matches {latest_existing_date}",
                },
            )
            print(f"Ignored 009805 provider date {date_key}; holdings still match {latest_existing_date}.")
            return

    payload = {
        "date": date_key,
        "meta": meta,
        "holdings": holdings,
    }

    previous = history.get(date_key)
    is_new_date = date_key not in history
    changed = previous != payload
    history[date_key] = payload
    _write_json(HISTORY_FILE, dict(sorted(history.items())))
    
    _write_json(
        LOG_FILE,
        {
            "last_checked_utc": now_utc,
            "last_updated_utc": now_utc if is_new_date else previous_log.get("last_updated_utc"),
            "latest_date": date_key,
            "status": "NEW DATA FOUND" if is_new_date else "NO CHANGE",
            "source": URL,
            "holdings_count": len(holdings),
            "payload_changed": changed,
        },
    )

    if is_new_date:
        print(f"Successfully updated 009805 holdings for {date_key}. Total stocks: {len(holdings)}")
    elif changed:
        print(f"Updated same-date 009805 payload for {date_key}; status remains NO CHANGE.")
    else:
        print(f"No holding changes detected for 009805. Latest stored date remains {date_key}.")

if __name__ == "__main__":
    fetch_and_update_009805()
