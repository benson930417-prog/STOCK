import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from playwright.sync_api import sync_playwright


DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
HISTORY_FILE = os.path.join(DATA_DIR, "etf_00997A_history.json")
LOG_FILE = os.path.join(DATA_DIR, "etf_00997A_log.json")

ETF_TICKER = "00997A"
PRODUCT_ID = "502"
PORTFOLIO_URL = f"https://www.capitalfund.com.tw/etf/product/detail/{PRODUCT_ID}/portfolio"


def _save_log(log_data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(log_data, f, ensure_ascii=False, indent=2)


def _parse_money(value):
    if value is None or pd.isna(value):
        return None
    text = str(value)
    text = re.sub(r"^[A-Z]{3}\s+", "", text).replace(",", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def _parse_int(value):
    if value is None or pd.isna(value):
        return None
    text = str(value).replace(",", "").replace(".0", "").strip()
    try:
        return int(float(text))
    except ValueError:
        return None


def _parse_weight(value):
    if value is None or pd.isna(value):
        return None
    text = str(value).replace("%", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def _yahoo_daily_dates(symbol, tz_name, range_days=45):
    try:
        res = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={range_days}d&interval=1d",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=5,
        )
        if res.status_code != 200:
            return set()
        chart = res.json()["chart"]["result"][0]
        timestamps = chart.get("timestamp", [])
        closes = chart.get("indicators", {}).get("quote", [{}])[0].get("close", [])
        return {
            datetime.fromtimestamp(timestamp, ZoneInfo(tz_name)).date()
            for timestamp, close in zip(timestamps, closes)
            if timestamp is not None and close is not None
        }
    except Exception:
        return set()


def _recent_global_trading_dates():
    dates = set()
    dates.update(_yahoo_daily_dates("SPY", "America/New_York"))
    dates.update(_yahoo_daily_dates("2330.TW", "Asia/Taipei"))
    dates.update(_yahoo_daily_dates("7203.T", "Asia/Tokyo"))
    return dates


def _is_global_trading_date(day, known_dates):
    return day in known_dates or day.weekday() < 5


def _previous_global_trading_date(before_day, known_dates):
    day = before_day - timedelta(days=1)
    while not _is_global_trading_date(day, known_dates):
        day -= timedelta(days=1)
    return day


def _candidate_holding_date(now_tw=None):
    now_tw = now_tw or datetime.now(ZoneInfo("Asia/Taipei"))
    known_dates = _recent_global_trading_dates()
    today = now_tw.date()
    after_official_update = now_tw.hour > 15 or (now_tw.hour == 15 and now_tw.minute >= 0)

    if after_official_update and _is_global_trading_date(today, known_dates):
        latest_disclosure_day = today
    else:
        latest_disclosure_day = _previous_global_trading_date(today, known_dates)

    return _previous_global_trading_date(latest_disclosure_day, known_dates).strftime("%Y-%m-%d")


def _closing_price_date_for_holding_date(holding_date_str):
    return holding_date_str


def _download_official_workbook():
    with tempfile.TemporaryDirectory() as tmpdir:
        download_path = Path(tmpdir) / f"{ETF_TICKER}.xlsx"
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
            context = browser.new_context(
                accept_downloads=True,
                locale="zh-TW",
                timezone_id="Asia/Taipei",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/123.0.0.0 Safari/537.36"
                ),
            )
            page = context.new_page()
            page.goto(PORTFOLIO_URL, wait_until="load", timeout=60000)
            try:
                page.wait_for_selector("button.buyback-search-section-btn", timeout=30000)
            except Exception as exc:
                title = page.title()
                current_url = page.url
                content_len = len(page.content())
                raise ValueError(
                    "Official download button not found after page load "
                    f"(url={current_url}, title={title!r}, content_len={content_len})"
                ) from exc

            body_text = page.locator("body").inner_text(timeout=10000)
            date_matches = re.findall(r"20\d{2}/\d{1,2}/\d{1,2}", body_text)
            official_page_date = None
            if date_matches:
                official_page_date = datetime.strptime(date_matches[0], "%Y/%m/%d").strftime("%Y-%m-%d")

            with page.expect_download(timeout=30000) as download_info:
                page.locator("button.buyback-search-section-btn").click()
            download = download_info.value
            download.save_as(download_path)
            browser.close()

        with open(download_path, "rb") as f:
            return official_page_date, f.read()


def _parse_workbook(workbook_bytes):
    from io import BytesIO

    excel = BytesIO(workbook_bytes)
    meta_df = pd.read_excel(excel, sheet_name="投資組合", header=None)

    fund_size = None
    nav = None
    for _, row in meta_df.iterrows():
        label = str(row.iloc[0])
        value = row.iloc[1] if len(row) > 1 else None
        if "基金淨資產價值" in label:
            fund_size = _parse_money(value)
        elif "每受益權單位淨資產價值" in label:
            nav = _parse_money(value)

    stocks_df = pd.read_excel(BytesIO(workbook_bytes), sheet_name="股票")
    holdings = []
    for _, row in stocks_df.iterrows():
        sid = str(row.get("股票代號", "")).strip()
        name = str(row.get("股票名稱", "")).strip()
        if not sid or sid.lower() == "nan" or not name or name.lower() == "nan":
            continue
        holdings.append({
            "id": sid,
            "name": name,
            "weight_pct": _parse_weight(row.get("持股權重(%)")),
            "shares": _parse_int(row.get("股數")),
        })

    if not holdings:
        raise ValueError("No valid stock holdings found in official workbook")

    return fund_size, nav, holdings


def _get_closing_price(file_date_str, nav):
    closing_price = nav
    try:
        res = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ETF_TICKER}.TW?range=14d&interval=1d",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=5,
        )
        if res.status_code == 200:
            chart = res.json()["chart"]["result"][0]
            ts_list = chart.get("timestamp", [])
            close_list = chart.get("indicators", {}).get("quote", [{}])[0].get("close", [])
            for idx in range(len(ts_list) - 1, -1, -1):
                dt_str = datetime.fromtimestamp(ts_list[idx], timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
                if dt_str == file_date_str and close_list[idx] is not None:
                    closing_price = float(close_list[idx])
                    break
    except Exception:
        pass
    return closing_price


def _holdings_fingerprint(day_data):
    holdings = day_data.get("holdings", [])
    normalized = [
        {
            "id": str(item.get("id", "")).strip(),
            "name": str(item.get("name", "")).strip(),
            "weight_pct": item.get("weight_pct"),
            "shares": item.get("shares"),
        }
        for item in holdings
    ]
    normalized.sort(key=lambda item: item["id"])
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True)


def _latest_history_day(history):
    if not history:
        return None, None
    latest_date = max(history.keys())
    return latest_date, history[latest_date]


def fetch_and_update_00997A():
    now_utc = datetime.now(timezone.utc).isoformat()
    log_data = {"last_checked_utc": None, "last_updated_utc": None, "status": "Initializing"}
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                log_data = json.load(f)
        except Exception:
            pass
    log_data["last_checked_utc"] = now_utc

    try:
        official_page_date, workbook_bytes = _download_official_workbook()
        file_date_str = _candidate_holding_date()
        fund_size, nav, holdings = _parse_workbook(workbook_bytes)
        closing_price_date = _closing_price_date_for_holding_date(file_date_str)
        closing_price = _get_closing_price(closing_price_date, nav)

        day_data = {
            "date": file_date_str,
            "meta": {
                "fund_size": fund_size,
                "nav": nav,
                "closing_price": float(closing_price) if closing_price is not None else None,
                "closing_price_date": closing_price_date,
            },
            "holdings": holdings,
        }

        history = {}
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                    history = json.load(f)
            except Exception:
                pass

        is_changed = True
        latest_existing_date, latest_existing_day_data = _latest_history_day(history)
        if latest_existing_day_data and _holdings_fingerprint(day_data) == _holdings_fingerprint(latest_existing_day_data):
            is_changed = False

        existing_day_data = history.get(file_date_str)
        if is_changed and existing_day_data:
            import copy

            curr_cmp = copy.deepcopy(day_data)
            prev_cmp = copy.deepcopy(existing_day_data)
            curr_cmp["meta"].pop("closing_price", None)
            curr_cmp["meta"].pop("closing_price_date", None)
            prev_cmp["meta"].pop("closing_price", None)
            prev_cmp["meta"].pop("closing_price_date", None)
            if json.dumps(curr_cmp, sort_keys=True) == json.dumps(prev_cmp, sort_keys=True):
                is_changed = False

        meta_changed = False
        if existing_day_data:
            meta_changed = json.dumps(
                existing_day_data.get("meta", {}),
                ensure_ascii=False,
                sort_keys=True,
            ) != json.dumps(day_data.get("meta", {}), ensure_ascii=False, sort_keys=True)

        history[file_date_str] = day_data
        os.makedirs(DATA_DIR, exist_ok=True)
        if is_changed or meta_changed:
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
            log_data["last_updated_utc"] = now_utc
            log_data["status"] = "NEW DATA FOUND" if is_changed else "META UPDATED"
            if is_changed:
                print(f"Successfully updated {ETF_TICKER} holdings for {file_date_str}. Total stocks: {len(holdings)}")
            else:
                print(
                    f"Updated {ETF_TICKER} metadata for {file_date_str} "
                    f"(closing_price_date={closing_price_date}, closing_price={closing_price})."
                )
        else:
            log_data["status"] = "No Change"
            log_data["official_page_date"] = official_page_date
            log_data["candidate_holding_date"] = file_date_str
            print(
                f"No holding changes detected for {ETF_TICKER}. "
                f"Latest stored date remains {latest_existing_date or 'none'} "
                f"(official_page_date={official_page_date}, candidate_holding_date={file_date_str})."
            )

    except Exception as e:
        log_data["status"] = f"Error: {e}"
        print(f"Error fetching/parsing {ETF_TICKER} data: {e}")

    _save_log(log_data)


if __name__ == "__main__":
    fetch_and_update_00997A()
