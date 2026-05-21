import json
import os
import re
from datetime import datetime, timezone

import requests
from playwright.sync_api import sync_playwright


DATA_DIR = "data"
HISTORY_FILE = os.path.join(DATA_DIR, "passive_0050_history.json")
LOG_FILE = os.path.join(DATA_DIR, "passive_0050_log.json")
URL = "https://www.yuantaetfs.com/product/detail/0050/ratio"
NAV_HISTORY_URL = "https://www.yuantaetfs.com/tradeInfo/comparison/0050/NAVhistory#table"


def _num(value):
    text = str(value or "").replace(",", "").replace("NTD", "").replace("$", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _date_to_key(value):
    match = re.search(r"(\d{4})/(\d{2})/(\d{2})", str(value or ""))
    if not match:
        raise ValueError(f"Could not parse 0050 date from {value!r}")
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"


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


def _extract_page_data(page):
    for _ in range(8):
        clicked = page.evaluate(
            """() => {
                const tableTitle = [...document.querySelectorAll('h3')]
                    .find(node => node.innerText.includes('\\u57fa\\u91d1\\u6b0a\\u91cd-\\u80a1\\u7968'));
                if (!tableTitle) return false;
                const tableBox = tableTitle.closest('.tt-list')?.parentElement;
                const more = [...tableBox.querySelectorAll('.moreBtn')]
                    .find(node => node.offsetParent !== null && node.innerText.includes('\\u5c55\\u958b'));
                if (!more) return false;
                more.click();
                return true;
            }"""
        )
        if not clicked:
            break
        page.wait_for_timeout(400)

    data = page.evaluate(
        """() => {
            const text = document.body.innerText;
            const tableTitle = [...document.querySelectorAll('h3')]
                .find(node => node.innerText.includes('\\u57fa\\u91d1\\u6b0a\\u91cd-\\u80a1\\u7968'));
            if (!tableTitle) throw new Error('Missing stock weight table');
            const tableBox = tableTitle.closest('.tt-list')?.parentElement;
            const rows = [...tableBox.querySelectorAll('.tbody .tr')].map(row => {
                return [...row.children].map(cell => {
                    const spans = cell.querySelectorAll('span');
                    return (spans.length ? spans[spans.length - 1] : cell).innerText.trim();
                });
            }).filter(row => row.length >= 4);
            return { text, rows };
        }"""
    )

    text = data["text"]
    rows = data["rows"]
    date_key = _date_to_key(text)

    def money_after(label):
        match = re.search(label + r"\s*NTD\s*\$?([0-9,]+(?:\.\d+)?)", text)
        return _num(match.group(1)) if match else None

    fund_size = money_after(r"\u57fa\u91d1\u8cc7\u7522\u7e3d\u6de8\u503c\(\u65b0\u53f0\u5e63\)")
    nav_match = re.search(
        r"\u57fa\u91d1\u6bcf\u55ae\u4f4d\u6de8\u503c\(\u65b0\u53f0\u5e63\)\s*NTD\s*\$?([0-9,]+(?:\.\d+)?)",
        text,
    )
    units_match = re.search(
        r"\u57fa\u91d1\u5728\u5916\u6d41\u901a\u55ae\u4f4d\u6578\(\u55ae\u4f4d\)\s*([0-9,]+)",
        text,
    )

    holdings = []
    for code, name, shares, weight in rows:
        holdings.append(
            {
                "id": code,
                "name": name,
                "weight_pct": float(str(weight).replace(",", "")),
                "shares": int(float(str(shares).replace(",", ""))),
            }
        )

    if len(holdings) < 10:
        raise ValueError(f"Only parsed {len(holdings)} 0050 holdings; expected expanded full table")

    return date_key, {
        "date": date_key,
        "meta": {
            "fund_size": fund_size,
            "nav": _num(nav_match.group(1)) if nav_match else None,
            "outstanding_units": int(_num(units_match.group(1))) if units_match else None,
            "source_url": URL,
        },
        "holdings": holdings,
    }


def _pct_change(curr, prev):
    if curr is None or prev in (None, 0):
        return None
    return ((curr - prev) / prev) * 100.0


def _get_yahoo_closing_price(ticker, date_str):
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        period1 = int(dt.timestamp())
        period2 = period1 + 86400
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&period1={period1}&period2={period2}"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        data = r.json()
        closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        if closes and closes[0] is not None:
            return round(float(closes[0]), 2)
        return None
    except Exception:
        return None


def _extract_nav_history(page):
    text = page.locator("body").inner_text(timeout=10000)
    table_start = text.find("淨值日期")
    if table_start < 0:
        raise ValueError("Missing 0050 NAV history table")

    table_text = text[table_start:]
    row_pattern = re.compile(
        r"(\d{4}/\d{2}/\d{2})\s+"
        r"([0-9,]+(?:\.\d+)?)\s+"
        r"([0-9,]+(?:\.\d+)?)\s+"
        r"([0-9,]+(?:\.\d+)?)\(([0-9,]+(?:\.\d+)?)%\)\s+"
        r"([0-9,]+)\s+"
        r"([0-9,]+)"
    )

    rows = []
    for match in row_pattern.finditer(table_text):
        premium = _num(match.group(4))
        rows.append(
            {
                "date": _date_to_key(match.group(1)),
                "nav": _num(match.group(2)),
                "closing_price": _num(match.group(3)),
                "premium_discount": premium,
                "premium_discount_pct": _num(match.group(5)),
                "fund_net_assets": int(_num(match.group(6))),
                "outstanding_units": int(_num(match.group(7))),
            }
        )

    if len(rows) < 2:
        raise ValueError(f"Only parsed {len(rows)} 0050 NAV rows; expected at least 2")

    latest = rows[0]
    previous = rows[1]
    latest["deltas"] = {
        "nav_pct": _pct_change(latest["nav"], previous["nav"]),
        "closing_price_pct": _pct_change(latest["closing_price"], previous["closing_price"]),
        "fund_net_assets_pct": _pct_change(latest["fund_net_assets"], previous["fund_net_assets"]),
        "outstanding_units_pct": _pct_change(latest["outstanding_units"], previous["outstanding_units"]),
    }
    return {
        "source_url": NAV_HISTORY_URL,
        "latest": latest,
        "previous": previous,
    }


def fetch_and_update_0050():
    now_utc = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    history = _load_json(HISTORY_FILE, {})
    previous_log = _load_json(LOG_FILE, {})

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36"
            )
        )
        page.goto(URL, wait_until="networkidle", timeout=60000)
        date_key, payload = _extract_page_data(page)
        page.goto(NAV_HISTORY_URL, wait_until="networkidle", timeout=60000)
        try:
            nav_history = _extract_nav_history(page)
        except Exception as exc:
            nav_history = None
            payload["meta"]["nav_history_error"] = str(exc)
            closing_price = _get_yahoo_closing_price("0050.TW", date_key)
            if closing_price is not None:
                payload["meta"]["closing_price"] = closing_price
            print(f"Warning: 0050 NAV history unavailable; keeping holdings update. {exc}")
        browser.close()

    if nav_history:
        latest_nav = nav_history["latest"]
        payload["meta"].update(
            {
                "fund_size": latest_nav["fund_net_assets"],
                "nav": latest_nav["nav"],
                "closing_price": latest_nav["closing_price"],
                "outstanding_units": latest_nav["outstanding_units"],
                "premium_discount": latest_nav["premium_discount"],
                "premium_discount_pct": latest_nav["premium_discount_pct"],
                "nav_history": nav_history,
            }
        )

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
            "holdings_count": len(payload["holdings"]),
        },
    )

    if changed:
        print(f"Successfully updated 0050 holdings for {date_key}. Total stocks: {len(payload['holdings'])}")
    else:
        print(f"No holding changes detected for 0050. Latest stored date remains {date_key}.")


if __name__ == "__main__":
    fetch_and_update_0050()
