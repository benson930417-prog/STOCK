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

def fetch_and_update_009805():
    now_utc = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    history = _load_json(HISTORY_FILE, {})
    previous_log = _load_json(LOG_FILE, {})

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    }

    # Fetch main page to get NAV and Market Price from carousel
    r_main = requests.get("https://www.tsit.com.tw/ETF/", headers=headers, verify=False, timeout=15)
    r_main.raise_for_status()
    soup_main = BeautifulSoup(r_main.text, 'html.parser')

    # 1. Extract Date, NAV, and Market Price from the carousel
    # Look for the block containing "009805"
    date_key = None
    nav = None
    closing_price = None

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
                        elif label1 == "市價":
                            closing_price = _num(val1)
            break

    if not date_key or nav is None or closing_price is None:
        raise ValueError("Failed to parse date, NAV, or closing price from the carousel.")

    meta = {
        "fund_size": None, # Optional as per user
        "nav": nav,
        "outstanding_units": None, # Optional as per user
        "closing_price": closing_price,
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
        print(f"Successfully updated 009805 holdings for {date_key}. Total stocks: {len(holdings)}")
    else:
        print(f"No holding changes detected for 009805. Latest stored date remains {date_key}.")

if __name__ == "__main__":
    fetch_and_update_009805()
