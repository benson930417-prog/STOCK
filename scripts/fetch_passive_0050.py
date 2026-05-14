import json
import os
import re
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright


DATA_DIR = "data"
HISTORY_FILE = os.path.join(DATA_DIR, "passive_0050_history.json")
LOG_FILE = os.path.join(DATA_DIR, "passive_0050_log.json")
URL = "https://www.yuantaetfs.com/product/detail/0050/ratio"


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
        browser.close()

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
