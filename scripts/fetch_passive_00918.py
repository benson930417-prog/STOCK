import json
import os
import re
from datetime import datetime, timezone

import requests
import urllib3


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


DATA_DIR = "data"
TICKER = "00918"
FUND_ID = "88329556"
HISTORY_FILE = os.path.join(DATA_DIR, f"passive_{TICKER}_history.json")
LOG_FILE = os.path.join(DATA_DIR, f"passive_{TICKER}_log.json")
URL = f"https://www.uobam.com.tw/fund/etf/{FUND_ID}"
PCF_API_URL = "https://www.uobam.com.tw/json/reply/WebSitePcfRequest"
PCF_EXCEL_URL = f"https://www.uobam.com.tw/api/WebSite/pcfexcel/{FUND_ID}"


def _num(value):
    text = str(value or "").replace(",", "").replace("NT$", "").replace("$", "").replace("%", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _date_to_key(value):
    text = str(value or "")
    match = re.search(r"/Date\((\d+)(?:[+-]\d{4})?\)/", text)
    if match:
        ts = int(match.group(1)) / 1000
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    match = re.search(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", text)
    if match:
        return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    raise ValueError(f"Could not parse {TICKER} date from {value!r}")


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


def _fetch_pcf():
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
    }
    response = requests.get(PCF_API_URL, params={"fundID": FUND_ID}, headers=headers, timeout=20, verify=False)
    response.raise_for_status()
    response.encoding = "utf-8"
    data = response.json()
    if str(data.get("etf002") or "").strip() != TICKER:
        raise ValueError(f"UOBAM PCF response is not {TICKER}: {data.get('etf002')!r}")
    return data


def _build_payload(data):
    date_key = _date_to_key(data.get("datadate") or data.get("publish") or data.get("announce"))
    holdings = []
    for item in data.get("result") or []:
        if str(item.get("kind") or "").lower() != "stock":
            continue
        code = str(item.get("code") or "").strip().upper()
        if not code:
            continue
        weight = _num(item.get("weight"))
        if weight is None or weight <= 0:
            continue
        shares = int(_num(item.get("qty")) or 0)
        holdings.append(
            {
                "id": f"{code}.TW" if code.isdigit() else code,
                "name": str(item.get("cName") or "").strip(),
                "weight_pct": weight,
                "shares": shares,
            }
        )

    if len(holdings) < 10:
        raise ValueError(f"Only parsed {len(holdings)} {TICKER} stock holdings from UOBAM PCF API")

    return date_key, {
        "date": date_key,
        "meta": {
            "fund_size": int(_num(data.get("totalAV")) or 0) or None,
            "nav": _num(data.get("nav")),
            "outstanding_units": int(_num(data.get("totalIssues")) or 0) or None,
            "creation_redemption_unit": int(_num(data.get("baseValue")) or 0) or None,
            "estimated_cash_component": _num(data.get("estcValue")),
            "basket_value": _num(data.get("basketValueP") or data.get("basketValue")),
            "source_url": URL,
            "pcf_excel_url": f"{PCF_EXCEL_URL}/{date_key.replace('-', '')}",
        },
        "holdings": holdings,
    }


def fetch_and_update_00918():
    now_utc = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    history = _load_json(HISTORY_FILE, {})
    previous_log = _load_json(LOG_FILE, {})

    data = _fetch_pcf()
    date_key, payload = _build_payload(data)

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
            "source": PCF_API_URL,
            "holdings_count": len(payload["holdings"]),
            "payload_changed": changed,
        },
    )

    if is_new_date:
        print(f"Successfully updated {TICKER} holdings for {date_key}. Total stocks: {len(payload['holdings'])}")
    elif changed:
        print(f"Updated same-date {TICKER} payload for {date_key}; status remains NO CHANGE.")
    else:
        print(f"No holding changes detected for {TICKER}. Latest stored date remains {date_key}.")


if __name__ == "__main__":
    fetch_and_update_00918()
