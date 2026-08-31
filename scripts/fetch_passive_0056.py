import json
import os
import re
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright


DATA_DIR = "data"
TICKER = "0056"
HISTORY_FILE = os.path.join(DATA_DIR, "passive_0056_history.json")
LOG_FILE = os.path.join(DATA_DIR, "passive_0056_log.json")
URL = "https://www.yuantaetfs.com/product/detail/0056/ratio"
NAV_HISTORY_URL = "https://www.yuantaetfs.com/tradeInfo/comparison/0056/NAVhistory#table"


HOLDINGS_TABLE_READY = r"""() => {
    const headings = [...document.querySelectorAll('h3')];
    const named = headings.find(node => {
        const text = (node.innerText || '').replace(/\s+/g, '').toLowerCase();
        return text.includes('\u57fa\u91d1\u6b0a\u91cd-\u80a1\u7968')
            || text.includes('fundholding')
            || text.includes('stockholding');
    });
    const structural = [...headings].reverse().find(node => {
        const box = node.closest('.tt-list')?.parentElement;
        if (!box || !box.querySelector('.moreBtn')) return false;
        return [...box.querySelectorAll('.tbody .tr')].filter(row => {
            const cells = [...row.children];
            return cells.length >= 4 && /^\d{4,6}[a-z]?$/i.test(cells[0].innerText.trim());
        }).length >= 5;
    });
    const heading = named || structural;
    if (!heading) return false;
    const box = heading.closest('.tt-list')?.parentElement;
    return !!box && [...box.querySelectorAll('.tbody .tr')].filter(
        row => row.children.length >= 4
    ).length >= 5;
}"""


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
        raise ValueError(f"Could not parse {TICKER} date from {value!r}")
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


def _wait_for_holdings_table(page):
    """Wait for rows from Yuanta's localized, client-rendered table."""

    for attempt in range(6):
        try:
            page.wait_for_function(HOLDINGS_TABLE_READY, timeout=6000)
            return
        except Exception:
            pass
        page.wait_for_timeout(1200)
        if attempt in {1, 3}:
            try:
                page.reload(wait_until="domcontentloaded", timeout=60000)
            except Exception:
                pass
    raise ValueError(f"Missing hydrated {TICKER} stock weight table after retries")


def _extract_page_data(page):
    _wait_for_holdings_table(page)
    for _ in range(8):
        clicked = page.evaluate(
            """() => {
                const headings = [...document.querySelectorAll('h3')];
                const tableTitle = headings.find(node => {
                    const text = (node.innerText || '').replace(/\\s+/g, '').toLowerCase();
                    return text.includes('\\u57fa\\u91d1\\u6b0a\\u91cd-\\u80a1\\u7968')
                        || text.includes('fundholding')
                        || text.includes('stockholding');
                }) || [...headings].reverse().find(node => {
                    const box = node.closest('.tt-list')?.parentElement;
                    return !!box?.querySelector('.moreBtn')
                        && box.querySelectorAll('.tbody .tr').length >= 5;
                });
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
            const headings = [...document.querySelectorAll('h3')];
            const tableTitle = headings.find(node => {
                const value = (node.innerText || '').replace(/\\s+/g, '').toLowerCase();
                return value.includes('\\u57fa\\u91d1\\u6b0a\\u91cd-\\u80a1\\u7968')
                    || value.includes('fundholding')
                    || value.includes('stockholding');
            }) || [...headings].reverse().find(node => {
                const box = node.closest('.tt-list')?.parentElement;
                return !!box?.querySelector('.moreBtn')
                    && box.querySelectorAll('.tbody .tr').length >= 5;
            });
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

    fund_size = money_after(r"基金資產總淨值\(新台幣\)")
    nav_match = re.search(
        r"基金每單位淨值\(新台幣\)\s*NTD\s*\$?([0-9,]+(?:\.\d+)?)",
        text,
    )
    units_match = re.search(
        r"基金在外流通單位數\(單位\)\s*([0-9,]+)",
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
        raise ValueError(f"Only parsed {len(holdings)} {TICKER} holdings; expected expanded full table")

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


def _wait_for_nav_table(page):
    """The NAV-history page is a SPA whose table hydrates after networkidle, and
    sometimes only once the table view is selected. Poll for the header, nudging
    the table tab into view between attempts, instead of reading the body blindly."""
    for attempt in range(6):
        try:
            page.wait_for_function(
                "() => document.body.innerText.includes('\\u6de8\\u503c\\u65e5\\u671f')",
                timeout=5000,
            )
            return
        except Exception:
            pass
        try:
            page.evaluate(
                """() => {
                    const tab = [...document.querySelectorAll('a, button, .tab, li, span')]
                        .find(node => node.offsetParent !== null
                            && node.innerText
                            && node.innerText.includes('\\u8868\\u683c'));
                    if (tab) tab.click();
                }"""
            )
        except Exception:
            pass
        page.wait_for_timeout(1500)
        if attempt == 3:
            try:
                page.reload(wait_until="networkidle", timeout=60000)
            except Exception:
                pass


def _extract_nav_history(page):
    _wait_for_nav_table(page)
    text = page.locator("body").inner_text(timeout=10000)
    table_start = text.find("淨值日期")
    if table_start < 0:
        raise ValueError(f"Missing {TICKER} NAV history table")

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
        rows.append(
            {
                "date": _date_to_key(match.group(1)),
                "nav": _num(match.group(2)),
                "fund_net_assets": int(_num(match.group(6))),
                "outstanding_units": int(_num(match.group(7))),
            }
        )

    if len(rows) < 2:
        raise ValueError(f"Only parsed {len(rows)} {TICKER} NAV rows; expected at least 2")

    latest = rows[0]
    previous = rows[1]
    latest["deltas"] = {
        "nav_pct": _pct_change(latest["nav"], previous["nav"]),
        "fund_net_assets_pct": _pct_change(latest["fund_net_assets"], previous["fund_net_assets"]),
        "outstanding_units_pct": _pct_change(latest["outstanding_units"], previous["outstanding_units"]),
    }
    return {
        "source_url": NAV_HISTORY_URL,
        "latest": latest,
        "previous": previous,
    }


def fetch_and_update_0056():
    now_utc = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    history = _load_json(HISTORY_FILE, {})
    previous_log = _load_json(LOG_FILE, {})

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36"
            ),
            locale="zh-TW",
            extra_http_headers={"Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8"},
        )
        page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        date_key, payload = _extract_page_data(page)
        page.goto(NAV_HISTORY_URL, wait_until="domcontentloaded", timeout=60000)
        nav_history = _extract_nav_history(page)
        browser.close()

    latest_nav = nav_history["latest"]
    payload["meta"].update(
        {
            "fund_size": latest_nav["fund_net_assets"],
            "nav": latest_nav["nav"],
            "outstanding_units": latest_nav["outstanding_units"],
            "nav_history": nav_history,
        }
    )

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
    fetch_and_update_0056()
