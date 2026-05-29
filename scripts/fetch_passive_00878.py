import json
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
import requests

DATA_DIR = "data"
HISTORY_FILE = os.path.join(DATA_DIR, "passive_00878_history.json")
LOG_FILE = os.path.join(DATA_DIR, "passive_00878_log.json")
URL = "https://www.cathaysite.com.tw/ETF/detail/ECN?tab=etf3"
JSON_API_STOCK_LIST = "https://cwapi.cathaysite.com.tw/api/ETF/GetETFDetailStockList"
JSON_API_ASSETS = "https://cwapi.cathaysite.com.tw/api/ETF/GetETFAssets"

# Cathay internal FundCode for 00878 國泰永續高股息 (URL prefix "ECN").
# Verified empirically: 525B TWD AUM, 29 TW holdings, NAV ~27 — matches 00878.
FUND_CODE = "CN"


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


def _get_yahoo_closing_price(ticker, date_str):
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        period1 = int(dt.timestamp())
        period2 = period1 + 86400
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&period1={period1}&period2={period2}"
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
        data = r.json()
        closes = data['chart']['result'][0]['indicators']['quote'][0]['close']
        if closes and closes[0] is not None:
            return round(float(closes[0]), 2)
        return None
    except Exception:
        return None


def _fetch_latest_assets(headers, today_str, lookback_days=10):
    for i in range(lookback_days + 1):
        search_date = (
            datetime.strptime(today_str, "%Y-%m-%d") - timedelta(days=i)
        ).strftime("%Y-%m-%d")
        params_assets = {
            "FundCode": FUND_CODE,
            "SearchDate": search_date,
        }
        r_assets = requests.get(JSON_API_ASSETS, headers=headers, params=params_assets, timeout=15)
        r_assets.raise_for_status()
        assets_data = r_assets.json()
        if assets_data.get("success") and assets_data.get("result"):
            return assets_data["result"], search_date
    return None, None


def fetch_and_update_00878():
    now_utc = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    history = _load_json(HISTORY_FILE, {})
    previous_log = _load_json(LOG_FILE, {})

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
    }

    tw_now = datetime.now(timezone.utc) + timedelta(hours=8)
    today_str = tw_now.strftime("%Y-%m-%d")

    # 1. Fetch ETF Assets (NAV, Fund Size, True Date). Cathay may return
    # "查無資料" on weekends/holidays, so walk back to the latest available day.
    result_assets, queried_date = _fetch_latest_assets(headers, today_str)
    if not result_assets:
        latest_date = previous_log.get("latest_date") or (max(history) if history else None)
        _write_json(
            LOG_FILE,
            {
                "last_checked_utc": now_utc,
                "last_updated_utc": previous_log.get("last_updated_utc"),
                "latest_date": latest_date,
                "status": "NO DATA",
                "source": URL,
                "message": "Cathay API returned no asset data in recent lookback window",
            },
        )
        print("No 00878 asset data available from Cathay API in recent lookback window.")
        return

    pre_date_raw = result_assets.get("preDate")
    if not pre_date_raw:
        raise ValueError("Missing preDate in assets response")

    date_key = pre_date_raw.replace("/", "-")

    fund_size = _num(result_assets.get("fundNav"))
    nav = _num(result_assets.get("fundPerNav"))
    outstanding_units = int(_num(result_assets.get("fundOutstandingShares")) or 0) or None
    closing_price = _get_yahoo_closing_price("00878.TW", date_key)

    meta = {
        "fund_size": fund_size,
        "nav": nav,
        "outstanding_units": outstanding_units,
        "closing_price": closing_price,
        "source_url": URL,
    }

    # 2. Fetch Holdings from JSON API
    params_holdings = {
        "FundCode": FUND_CODE,
        "SearchDate": date_key
    }
    r_json = requests.get(JSON_API_STOCK_LIST, headers=headers, params=params_holdings, timeout=15)
    r_json.raise_for_status()
    api_data = r_json.json()

    if not api_data.get("success") or not api_data.get("result"):
        latest_date = previous_log.get("latest_date") or (max(history) if history else None)
        _write_json(
            LOG_FILE,
            {
                "last_checked_utc": now_utc,
                "last_updated_utc": previous_log.get("last_updated_utc"),
                "latest_date": latest_date,
                "status": "NO DATA",
                "source": URL,
                "message": f"Cathay API returned no holdings for {date_key}: {api_data.get('returnMessage')}",
            },
        )
        print(f"No 00878 holdings data available from Cathay API for {date_key}.")
        return

    holdings = []
    for item in api_data["result"]:
        code = str(item.get("stockCode") or "").strip().upper()
        if not code:
            continue
        # 00878 holds Taiwan equities only. Cathay's API returns plain numeric codes for TW listings.
        if "." not in code and code.isdigit():
            code = f"{code}.TW"

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
        raise ValueError(f"Only parsed {len(holdings)} 00878 holdings from API")

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
        print(f"Successfully updated 00878 holdings for {date_key}. Total stocks: {len(holdings)}")
    elif changed:
        print(f"Updated same-date 00878 payload for {date_key}; status remains NO CHANGE.")
    else:
        print(f"No holding changes detected for 00878. Latest stored date remains {date_key}.")


if __name__ == "__main__":
    fetch_and_update_00878()
