import json
import os
import re
from datetime import datetime, timedelta, timezone
from io import BytesIO

import pandas as pd
import requests


requests.packages.urllib3.disable_warnings()

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
ETF_TICKER = "00403A"
FUND_CODE = "63YTW"
HISTORY_FILE = os.path.join(DATA_DIR, f"etf_{ETF_TICKER}_history.json")
LOG_FILE = os.path.join(DATA_DIR, f"etf_{ETF_TICKER}_log.json")


def _num(value):
    text = str(value or "").replace("NTD", "").replace(",", "").replace("%", "").strip()
    if not text or text.lower() == "nan":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _date_from_text(text):
    match = re.search(r"(\d{3,4})[-/](\d{1,2})[-/](\d{1,2})", str(text or ""))
    if not match:
        return None
    year = int(match.group(1))
    if year < 1000:
        year += 1911
    return f"{year}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"


def _load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def _write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def _latest_yahoo_close(date_key):
    try:
        response = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ETF_TICKER}.TW?range=14d&interval=1d",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=5,
        )
        if response.status_code != 200:
            return None
        result = response.json()["chart"]["result"][0]
        timestamps = result.get("timestamp", [])
        closes = result.get("indicators", {}).get("quote", [{}])[0].get("close", [])
        for idx in range(len(timestamps) - 1, -1, -1):
            dt_str = datetime.fromtimestamp(timestamps[idx], timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
            if dt_str == date_key and closes[idx] is not None:
                return float(closes[idx])
    except Exception:
        return None
    return None


def fetch_and_update_holdings():
    now_utc = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    print(f"[{now_utc}] Fetching {ETF_TICKER} ETF holdings...")
    log_data = _load_json(LOG_FILE, {"last_checked_utc": None, "last_updated_utc": None, "status": "Initializing"})
    log_data["last_checked_utc"] = now_utc

    try:
        url = f"https://www.ezmoney.com.tw/ETF/Fund/AssetExcelNPOI?fundCode={FUND_CODE}"
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, verify=False, timeout=30)
        response.raise_for_status()
        df_raw = pd.read_excel(BytesIO(response.content), header=None)

        header_row = -1
        for i, row in df_raw.iterrows():
            row_text = " ".join(str(x) for x in row.values)
            if "股票代號" in row_text and "持股權重" in row_text:
                header_row = i
                break
        if header_row < 0:
            raise RuntimeError("Could not find 00403A stock holding header row")

        file_date = datetime.now().strftime("%Y-%m-%d")
        fund_size = None
        outstanding_units = None
        nav = None
        for i in range(header_row):
            values = df_raw.iloc[i].values
            row_text = " ".join(str(x) for x in values if pd.notna(x))
            if "資料日期" in row_text:
                file_date = _date_from_text(row_text) or file_date
            elif "淨資產" in row_text:
                fund_size = next((_num(v) for v in values if _num(v) is not None), fund_size)
            elif "流通在外單位數" in row_text:
                outstanding_units = next(
                    (
                        int(value)
                        for value in (_num(v) for v in values)
                        if value is not None
                    ),
                    outstanding_units,
                )
            elif "每單位淨值" in row_text:
                nav = next((_num(v) for v in values if _num(v) is not None), nav)

        df = df_raw.iloc[header_row + 1:].copy()
        df.columns = df_raw.iloc[header_row].fillna("").astype(str).tolist()
        df = df.dropna(how="all")

        holdings = []
        for row in df.to_dict("records"):
            sid = str(row.get("股票代號") or "").strip()
            name = str(row.get("股票名稱") or "").strip()
            weight = _num(row.get("持股權重"))
            shares = _num(row.get("股數"))
            if sid and sid.lower() != "nan" and name and name.lower() != "nan" and weight is not None:
                holdings.append({
                    "id": sid,
                    "name": name,
                    "weight_pct": weight,
                    "shares": int(shares or 0),
                })
        if len(holdings) < 10:
            raise RuntimeError(f"Only parsed {len(holdings)} holdings for {ETF_TICKER}")

        closing_price = _latest_yahoo_close(file_date)
        if closing_price is None:
            closing_price = nav
        day_data = {
            "date": file_date,
            "meta": {
                "fund_size": fund_size,
                "outstanding_units": outstanding_units,
                "nav": nav,
                "closing_price": float(closing_price) if closing_price is not None else None,
            },
            "holdings": holdings,
        }

        history = _load_json(HISTORY_FILE, {})
        previous = history.get(file_date)
        changed = previous != day_data
        history[file_date] = day_data
        _write_json(HISTORY_FILE, dict(sorted(history.items())))

        log_data["status"] = "NEW DATA FOUND" if changed else "No Change"
        if changed:
            log_data["last_updated_utc"] = now_utc
            print(f"Successfully updated {ETF_TICKER} holdings for {file_date}. Total stocks: {len(holdings)}")
        else:
            print(f"No changes detected for {ETF_TICKER} on {file_date}.")
        _write_json(LOG_FILE, log_data)
    except Exception as exc:
        print(f"Error fetching/parsing {ETF_TICKER} data: {exc}")
        log_data["status"] = f"Error: {exc}"
        _write_json(LOG_FILE, log_data)
        raise


if __name__ == "__main__":
    fetch_and_update_holdings()
