import os
import asyncio
import urllib.request
import re
from io import BytesIO
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from playwright.async_api import async_playwright
from PIL import Image, ImageDraw, ImageFont

app = FastAPI()

# Configuration
CHART_TABS = {
    "oil": "https://www.tradingview.com/symbols/USOIL/?exchange=FXCM&timeframe=5D",
    "brent": "https://www.tradingview.com/symbols/RUS-BR1!/?timeframe=5D",
    "bond": "https://www.tradingview.com/symbols/TVC-US10Y/?timeframe=5D",
    "gold": "https://www.tradingview.com/symbols/GOLD/?timeframe=5D",
    "usdtwd": "https://www.tradingview.com/symbols/FX_IDC-USDTWD/?timeframe=5D",
    "usdjpy": "https://www.tradingview.com/symbols/OANDA-USDJPY/?timeframe=5D",
    "usdchf": "https://www.tradingview.com/symbols/OANDA-USDCHF/?timeframe=5D",
    # NASDAQ 24h CFD — intraday indicator, 1-day window (it trades round the clock).
    "nasdaq": "https://www.tradingview.com/symbols/IG-NASDAQ/?timeframe=1D",
}

# Chinese titles for each chart key
CHART_META = {
    "oil":    {"title": "WTI 輕原油 (5日)", "display_title": "輕原油", "emoji": "🛢️", "precision": 2, "unit": "美元"},
    "brent":  {"title": "布蘭特原油 (5日)", "display_title": "布蘭特原油", "emoji": "🛢️", "precision": 2, "unit": "美元"},
    "bond":   {"title": "10年期公債殖利率 (5日)", "display_title": "美國10年期公債殖利率", "emoji": "📈", "precision": 3, "unit": "%"},
    "gold":   {"title": "黃金現貨 (5日)", "display_title": "黃金 GOLD", "emoji": "🥇", "precision": 2, "unit": "USD"},
    "usdtwd": {"title": "美元兌台幣 (5日)", "display_title": "美元兌台幣", "emoji": "💵", "precision": 3, "unit": "台幣"},
    "usdjpy": {"title": "美元兌日幣 (5日)", "display_title": "美元兌日幣", "emoji": "💴", "precision": 2, "unit": "日圓"},
    "usdchf": {"title": "美元兌瑞郎 (5日)", "display_title": "美元兌瑞郎", "emoji": "💷", "precision": 4, "unit": "瑞郎"},
    # Intraday indicator: 1-day chart, show only the real-time change (no 5d/1m/6m).
    "nasdaq": {"title": "那斯達克 NASDAQ (即時)", "display_title": "那斯達克 NASDAQ", "emoji": "📈",
               "precision": 2, "unit": "", "perf_labels": [("1d", "即時漲跌：")], "perf_header": None},
}

PERFORMANCE_LABELS = {
    "1 day": "1d",
    "5 days": "5d",
    "1 week": "5d",
    "1 month": "1m",
    "6 months": "6m",
    "Year to date": "ytd",
    "1 year": "1y",
    "5 years": "5y",
    "10 years": "10y",
    "All time": "all",
}

TRADINGVIEW_SCANNER_QUOTES = {
    "oil": {
        "candidates": [
            {"scanner": "futures", "symbol": "NYMEX:CL1!"},
            {"scanner": "cfd", "symbol": "TVC:USOIL"},
            {"scanner": "cfd", "symbol": "FXCM:USOIL"},
        ],
    },
    # Bond (US 10Y yield) — symbol per user request is CBOT_MINI:10Y1! (micro
    # 10-yr yield futures). TVC:US10Y is the cash yield CFD as a fallback —
    # tracks within ~0.005 of the futures and uses TradingView's standard
    # widget structure so scanner data is more reliable.
    "bond": {
        "price_from_page": True,
        "performance_candidate": {"scanner": "futures", "symbol": "CBOT_MINI:10Y1!"},
        "skip_dom_performance_overlay": True,
    },
    "usdtwd": {"scanner": "forex", "symbol": "FX_IDC:USDTWD"},
    "usdjpy": {"scanner": "forex", "symbol": "OANDA:USDJPY"},
    "usdchf": {"scanner": "forex", "symbol": "OANDA:USDCHF"},
    "nasdaq": {
        "candidates": [
            {"scanner": "cfd", "symbol": "IG:NASDAQ"},        # 24h CFD (primary)
            {"scanner": "futures", "symbol": "CME_MINI:NQ1!"}, # Nasdaq-100 futures fallback
            {"scanner": "america", "symbol": "NASDAQ:NDX"},    # cash index fallback
        ],
    },
}

OUTPUT_DIR = os.path.join(os.getcwd(), 'data', 'images')
os.makedirs(OUTPUT_DIR, exist_ok=True)

# --- CJK Font Setup (same as plot_tv_chart.py) ---
FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'fonts')
FONT_PATH = os.path.join(FONT_DIR, 'NotoSansTC-Regular.otf')

if not os.path.exists(FONT_PATH):
    os.makedirs(FONT_DIR, exist_ok=True)
    try:
        url = 'https://github.com/googlefonts/noto-cjk/raw/main/Sans/OTF/TraditionalChinese/NotoSansCJKtc-Regular.otf'
        urllib.request.urlretrieve(url, FONT_PATH)
        print(f"✅ Downloaded CJK font to {FONT_PATH}")
    except Exception as e:
        print(f"⚠️ Failed to auto-download CJK font: {e}")

# Global State
playwright_instance = None
browser_context = None
browser_instance = None
pages = {}

HIDE_CSS = """
    header, aside, nav, div[class*="layout__header"], div[class*="pageHead-"],
    div[class*="cookies-banner"], div[class*="cookie-banner"],
    div[class*="breadcrumb"], div[role="tablist"], div[class*="tabsRow-"],
    div[class*="buttonsRow-"], div[class*="quotesRow-"], div[class*="quotesSubLine-"],
    div[class*="sectionTitle-"], div[class*="fixed-banners"],
    a[aria-label="Full chart"], button[aria-label="Take a snapshot"], a[aria-label="Get widget"],
    div:has(> button[class*="rangeButton-"]),
    div[data-container-name="performance-chart-id"] > div[class*="header"],
    h2#performance-chart-id, h2[id="performance-chart-id"],
    div[data-container-name="performance-chart-id"] ~ *,
    div[class*="symbolHeader-"], div[class*="symbol-header"] {
        display: none !important;
    }
    body { overflow: hidden !important; }
"""


def _num(value):
    text = str(value or "")
    text = text.replace("−", "-").replace("\u202a", "").replace("\u202c", "")
    text = text.replace("\u202f", "").replace(",", "").replace("%", "").strip()
    text = re.sub(r"[^\d.+\-kKmM]", "", text)
    if not text:
        return None
    multiplier = 1.0
    if text[-1:].lower() == "k":
        multiplier = 1_000.0
        text = text[:-1]
    elif text[-1:].lower() == "m":
        multiplier = 1_000_000.0
        text = text[:-1]
    try:
        return float(text) * multiplier
    except ValueError:
        return None


def _parse_performance_from_text(text):
    raw = str(text or "")
    performance = {}
    aliases = {
        "1 day": "1d",
        "5 days": "5d",
        "1 week": "5d",
        "1 month": "1m",
        "6 months": "6m",
        "Year to date": "ytd",
        "1 year": "1y",
        "5 years": "5y",
        "10 years": "10y",
        "All time": "all",
    }
    for label, key in aliases.items():
        match = re.search(
            rf"{re.escape(label)}\s*([-+−]?[0-9][0-9,.]*%)",
            raw,
            flags=re.IGNORECASE,
        )
        if match:
            value = _num(match.group(1))
            if value is not None:
                performance[key] = value

    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    for idx, line in enumerate(lines[:-1]):
        key = PERFORMANCE_LABELS.get(line)
        if not key:
            continue
        value = _num(lines[idx + 1])
        if value is not None:
            performance[key] = value
    return performance


def _parse_market_text(text):
    raw = str(text or "")
    compact_patterns = [
        r"Market\s+open\s*([0-9][0-9,.]*)\s*%\s*(?:R)?\s*([-+−][0-9][0-9,.]*)\s*([-+−][0-9][0-9,.]*%)",
        r"([0-9][0-9,.]*)\s*%\s*(?:R)?\s*([-+−][0-9][0-9,.]*)\s*([-+−][0-9][0-9,.]*%)",
        r"Market\s+open\s*([0-9][0-9,.]*(?:[kKmM])?)\s*[A-Z]?\s*(USD|TWD|JPY|CHF|EUR|GBP|HKD)?(?:[\s\u2009\u202f/]*[A-Z]{2,5})?\s*[A-Z]?\s*([-+−][0-9][0-9,.]*(?:[kKmM])?)\s*([-+−][0-9][0-9,.]*%)",
        r"([0-9][0-9,.]*(?:[kKmM])?)\s*[A-Z]?\s*(USD|TWD|JPY|CHF|EUR|GBP|HKD)(?:[\s\u2009\u202f/]*[A-Z]{2,5})?\s*[A-Z]?\s*([-+−][0-9][0-9,.]*(?:[kKmM])?)\s*([-+−][0-9][0-9,.]*%)",
        r"([-+−]?[0-9][0-9,.]*(?:[kKmM])?)\s*(?:R)?\s*([-+−]?[0-9][0-9,.]*(?:[kKmM])?)\s*([-+−]?[0-9][0-9,.]*%)",
    ]
    compact_match = None
    for pattern in compact_patterns:
        compact_match = re.search(pattern, raw)
        if compact_match:
            break
    if compact_match:
        groups = compact_match.groups()
        if len(groups) == 4:
            price, currency, change_abs, change_pct = groups
        else:
            price, change_abs, change_pct = groups
            currency = ""
        currency = currency or ""
        if len(currency) > 3 and currency.endswith("R"):
            currency = currency[:-1]
        if _num(price) is None or _num(price) <= 0:
            raise ValueError("Could not parse a positive TradingView quote price")
        return {
            "price": _num(price),
            "currency": currency,
            "change_abs": _num(change_abs),
            "change_pct": _num(change_pct),
            "as_of_text": None,
            "performance": _parse_performance_from_text(text),
        }

    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    price = change_abs = change_pct = None
    currency = ""
    as_of = None
    for idx, line in enumerate(lines[:-4]):
        maybe_price = _num(lines[idx + 1])
        if maybe_price is None:
            continue
        maybe_currency = lines[idx + 2].strip()
        maybe_change_abs = _num(lines[idx + 3])
        maybe_change_pct = _num(lines[idx + 4])
        if maybe_change_abs is None or maybe_change_pct is None:
            continue
        if not re.fullmatch(r"[A-Z%]{1,5}", maybe_currency):
            continue
        price = maybe_price
        currency = maybe_currency
        change_abs = maybe_change_abs
        change_pct = maybe_change_pct
        if idx + 5 < len(lines) and lines[idx + 5].startswith("As of"):
            as_of = lines[idx + 5]
        break
    if price is None:
        raise ValueError("Could not parse TradingView market quote text")
    return {
        "price": price,
        "currency": currency,
        "change_abs": change_abs,
        "change_pct": change_pct,
        "as_of_text": as_of,
        "performance": _parse_performance_from_text(text),
    }


async def _extract_market_quote(page):
    return _parse_market_text(await _get_body_text(page))


async def _extract_ig_nasdaq_quote(page):
    """Parse the visible US Tech 100 Cash quote from the IG-NASDAQ page.

    The TradingView scanner can return a different CFD feed for IG:NASDAQ than
    the public symbol page displays. For this LINE command the user explicitly
    wants the page value from https://www.tradingview.com/symbols/IG-NASDAQ/.
    """
    text = await _get_body_text(page)
    marker = "US Tech 100 Cash"
    start = text.find(marker)
    if start < 0:
        raise ValueError("Could not find US Tech 100 Cash on IG-NASDAQ page")
    slice_text = text[start:start + 1000]
    match = re.search(
        r"US Tech 100 Cash.*?([0-9][0-9,.]*)\s*(?:D\s*)?USD(?:R)?\s*([+−-][0-9][0-9,.]*)\s*([+−-][0-9][0-9,.]*%)",
        slice_text,
        flags=re.S,
    )
    if not match:
        raise ValueError(f"Could not parse IG-NASDAQ page quote: {slice_text[:300]}")
    price, change_abs, change_pct = match.groups()
    return {
        "price": _num(price),
        "currency": "USD",
        "change_abs": _num(change_abs),
        "change_pct": _num(change_pct),
        "as_of_text": None,
        "performance": {},
    }


async def _fetch_tradingview_scanner_values(page, scanner, symbol, columns):
    response = await page.request.post(
        f"https://scanner.tradingview.com/{scanner}/scan",
        data={
            "symbols": {"tickers": [symbol], "query": {"types": []}},
            "columns": columns,
        },
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=10000,
    )
    if not response.ok:
        raise ValueError(f"TradingView scanner failed for {scanner}/{symbol}: HTTP {response.status} {await response.text()}")
    payload = await response.json()
    rows = payload.get("data") or []
    if not rows:
        raise ValueError(f"TradingView scanner failed for {scanner}/{symbol}: no rows")
    return rows[0].get("d") or []


async def _fetch_tradingview_scanner_quote(page, key):
    config = TRADINGVIEW_SCANNER_QUOTES.get(key)
    if not config:
        return None
    if config.get("price_from_page") and config.get("performance_candidate"):
        performance_candidate = config["performance_candidate"]
        quote = await _extract_market_quote(page)
        performance_values = await _fetch_tradingview_scanner_values(
            page,
            performance_candidate["scanner"],
            performance_candidate["symbol"],
            ["name", "close", "change", "change_abs", "currency", "Perf.W", "Perf.1M", "Perf.6M"],
        )
        if len(performance_values) < 4 or performance_values[1] is None:
            raise ValueError(f"TradingView scanner missing performance for {performance_candidate['scanner']}/{performance_candidate['symbol']}: {performance_values}")
        quote["change_pct"] = float(performance_values[2]) if performance_values[2] is not None else None
        quote["change_abs"] = float(performance_values[3]) if performance_values[3] is not None else None
        quote["performance"] = {
            "5d": float(performance_values[5]) if len(performance_values) > 5 and performance_values[5] is not None else None,
            "1m": float(performance_values[6]) if len(performance_values) > 6 and performance_values[6] is not None else None,
            "6m": float(performance_values[7]) if len(performance_values) > 7 and performance_values[7] is not None else None,
        }
        return quote
    columns = config.get("columns") or ["name", "close", "change", "change_abs", "currency", "Perf.W", "Perf.1M", "Perf.6M"]
    errors = []
    candidates = config.get("candidates") or [
        {"scanner": scanner, "symbol": config["symbol"]}
        for scanner in (config.get("scanners") or [config["scanner"]])
    ]
    for candidate in candidates:
        scanner = candidate["scanner"]
        symbol = candidate["symbol"]
        response = await page.request.post(
            f"https://scanner.tradingview.com/{scanner}/scan",
            data={
                "symbols": {"tickers": [symbol], "query": {"types": []}},
                "columns": columns,
            },
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10000,
        )
        if not response.ok:
            errors.append(f"{scanner}/{symbol}: HTTP {response.status} {await response.text()}")
            continue
        payload = await response.json()
        rows = payload.get("data") or []
        if not rows:
            errors.append(f"{scanner}/{symbol}: no rows")
            continue
        values = rows[0].get("d") or []
        if len(values) < 4 or values[1] is None:
            errors.append(f"{scanner}/{symbol}: missing close/change {values}")
            continue
        return {
            "price": float(values[1]),
            "currency": values[4] if len(values) > 4 and values[4] else "",
            "change_pct": float(values[2]) if values[2] is not None else None,
            "change_abs": float(values[3]) if values[3] is not None else None,
            "as_of_text": None,
            "performance": {
                "5d": float(values[5]) if len(values) > 5 and values[5] is not None else None,
                "1m": float(values[6]) if len(values) > 6 and values[6] is not None else None,
                "6m": float(values[7]) if len(values) > 7 and values[7] is not None else None,
            },
        }
    requested = ", ".join(f"{item['scanner']}/{item['symbol']}" for item in candidates)
    if config.get("allow_dom_fallback"):
        print(f"⚠️ TradingView scanner unavailable for {requested}; falling back to page text: {'; '.join(errors)}")
        return None
    raise ValueError(f"TradingView scanner failed for {requested}: {'; '.join(errors)}")


def _format_change(change_pct, label):
    sign = "+" if change_pct > 0 else ""
    direction = "🔴" if change_pct > 0 else "🟢"
    if change_pct == 0:
        direction = "⚪"
    return f"{label} {direction}{sign}{change_pct:.2f}%"


def _market_text_payload(key, quote):
    meta = CHART_META[key]
    price = quote["price"]
    unit = meta.get("unit") or quote.get("currency", "")
    title = meta.get("display_title", key)
    emoji = meta.get("emoji", "")
    precision = int(meta.get("precision", 2))
    performance = quote.get("performance") or {}
    labels = meta.get("perf_labels") or [
        ("1d", "1日："),
        ("5d", "1週："),
        ("1m", "1月："),
        ("6m", "6月："),
    ]
    price_line = f"🕒 最新報價：{price:,.{precision}f}" + (f" {unit}" if unit else "")
    lines = [
        f"{emoji} {title}".strip(),
        "──────────",
        price_line,
    ]
    # perf_header may be omitted (None/"") for a compact intraday indicator
    perf_header = meta.get("perf_header", "📊 近期漲跌幅：")
    if perf_header:
        lines += ["", perf_header]
    for perf_key, label in labels:
        value = performance.get(perf_key)
        if value is None and perf_key == "1d":
            value = quote.get("change_pct")
        if value is None:
            lines.append(f"{label} 無資料")
        else:
            lines.append(_format_change(float(value), label))
    return {
        "key": key,
        "text": "\n".join(lines),
        "quote": quote,
    }


def _debug_text_slice(text):
    raw = str(text or "")
    markers = ["Market open", "As of today", "1 day", "Previous close"]
    positions = [raw.find(marker) for marker in markers if raw.find(marker) >= 0]
    if not positions:
        return raw[:3000]
    start = max(0, min(positions) - 500)
    return raw[start:start + 4000]


def _overlay_title(image_path, title):
    """Draw a Chinese title bar on top of the chart screenshot."""
    img = Image.open(image_path).convert("RGB")
    w, h = img.size

    # Title bar dimensions
    bar_height = 70
    padding_x = 16
    padding_top = 14

    # Create new image with title bar on top
    new_img = Image.new("RGB", (w, h + bar_height), "#FFFFFF")
    new_img.paste(img, (0, bar_height))
    draw = ImageDraw.Draw(new_img)

    # Load font
    try:
        font_title = ImageFont.truetype(FONT_PATH, 28)
    except Exception:
        font_title = ImageFont.load_default()

    # Draw title text
    draw.text((padding_x, padding_top), title, fill="#1A1A2E", font=font_title)

    # Subtle separator line
    draw.line([(0, bar_height - 1), (w, bar_height - 1)], fill="#E5E5E5", width=1)

    new_img.save(image_path)
    print(f"  🖌️ Title overlay added: {title}")


def _trim_bottom_whitespace(image_path, padding=18, min_trim=24, max_body_height=430):
    """Normalize chart snapshots by trimming oversized blank lower areas."""
    img = Image.open(image_path).convert("RGB")
    width, height = img.size
    pixels = img.load()
    last_content_y = height - 1

    for y in range(height - 1, -1, -1):
        non_white = 0
        for x in range(width):
            r, g, b = pixels[x, y]
            if min(r, g, b) < 245:
                non_white += 1
                if non_white >= 8:
                    last_content_y = y
                    break
        if non_white >= 8:
            break

    crop_bottom = min(height, last_content_y + padding)
    if height > max_body_height:
        crop_bottom = min(crop_bottom, max_body_height)
    if height - crop_bottom >= min_trim:
        img.crop((0, 0, width, crop_bottom)).save(image_path)
        print(f"  ✂️ Trimmed bottom whitespace: {height - crop_bottom}px")


async def init_browser():
    global playwright_instance, browser_instance, browser_context, pages

    print("🧹 Cleaning up old browser instances...")
    if browser_instance:
        try:
            await browser_instance.close()
        except Exception as e:
            print(f"⚠️ Error closing browser_instance: {e}")
    if playwright_instance:
        try:
            await playwright_instance.stop()
        except Exception as e:
            print(f"⚠️ Error stopping playwright_instance: {e}")
    pages.clear()

    print("🚀 Initializing Browser...")
    playwright_instance = await async_playwright().start()
    browser_instance = await playwright_instance.chromium.launch(headless=True)
    browser_context = await browser_instance.new_context(
        viewport={'width': 600, 'height': 800},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        timezone_id="Asia/Taipei"
    )
    
    for key, url in CHART_TABS.items():
        print(f"  - Loading tab: {key}")
        page = await browser_context.new_page()
        try:
            await page.goto(url, wait_until="networkidle", timeout=60000)
            await page.add_style_tag(content=HIDE_CSS)
            await asyncio.sleep(2)
            pages[key] = page
        except Exception as e:
            print(f"❌ Failed to load {key}: {e}")

    print("✅ All tabs ready.")

@app.on_event("startup")
async def startup_event():
    await init_browser()

@app.on_event("shutdown")
async def shutdown_event():
    global playwright_instance, browser_instance
    if browser_instance:
        await browser_instance.close()
    if playwright_instance:
        await playwright_instance.stop()

class SnapshotRequest(BaseModel):
    key: str
    crop_x: float | None = None
    crop_y: float | None = None
    crop_width: float | None = None
    crop_height: float | None = None


async def _get_page_for_key(key):
    if key not in pages:
        await init_browser()
        if key not in pages:
            raise HTTPException(status_code=404, detail="Tab key not found")
    return pages[key]


async def _get_body_text(page):
    return await page.locator("body").evaluate("(body) => body.textContent || body.innerText || ''")


@app.post("/market-text")
async def market_text(req: SnapshotRequest):
    page = await _get_page_for_key(req.key)
    try:
        if req.key == "nasdaq":
            await page.goto(CHART_TABS[req.key], wait_until="networkidle", timeout=60000)
            await page.evaluate("window.scrollTo(0, 0)")
            await asyncio.sleep(0.8)
            quote = await _extract_ig_nasdaq_quote(page)
            return _market_text_payload(req.key, quote)
        quote = await _fetch_tradingview_scanner_quote(page, req.key)
        if quote is None:
            try:
                quote = await _extract_market_quote(page)
            except Exception as dom_error:
                print(f"⚠️ DOM quote extraction failed for {req.key}: {dom_error}")
                text = await _get_body_text(page)
                quote = _parse_market_text(text)
        elif not (TRADINGVIEW_SCANNER_QUOTES.get(req.key) or {}).get("skip_dom_performance_overlay"):
            # Scanner data is fast but its `Perf.W` is week-to-date (since
            # Monday), NOT the last 5 trading days. TradingView's on-page
            # widget displays "5 days" which IS the trailing 5d. To match
            # what the user sees on the actual page, overlay DOM-scraped
            # performance over scanner values where DOM provides them.
            try:
                dom_perf = _parse_performance_from_text(await _get_body_text(page))
                perf = quote.setdefault("performance", {})
                for k in ("1d", "5d", "1m", "6m"):
                    if dom_perf.get(k) is not None:
                        perf[k] = dom_perf[k]
            except Exception as e:
                print(f"⚠️ Could not overlay DOM perf for {req.key}: {e}")
        return _market_text_payload(req.key, quote)
    except Exception as e:
        print(f"❌ Error parsing market text for {req.key}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/market-debug")
async def market_debug(req: SnapshotRequest):
    debug = {"key": req.key, "ok": False}
    try:
        page = await _get_page_for_key(req.key)
    except Exception as exc:
        debug.update({"stage": "get_page", "error": str(exc), "loaded_keys": sorted(pages.keys())})
        return debug

    debug["url"] = getattr(page, "url", "")
    try:
        debug["title"] = await page.title()
    except Exception as exc:
        debug["title_error"] = str(exc)

    try:
        text = await _get_body_text(page)
    except Exception as exc:
        debug.update({"stage": "body_text", "error": str(exc)})
        return debug

    try:
        quote = await _extract_market_quote(page)
    except Exception as exc:
        quote = {"error": str(exc)}
    debug.update({
        "ok": "error" not in quote,
        "quote": quote,
        "body_text_head": text[:3000],
        "body_text_quote_slice": _debug_text_slice(text),
    })
    return debug

@app.post("/snapshot")
async def take_snapshot(req: SnapshotRequest):
    page = await _get_page_for_key(req.key)
    filename = f"{req.key}_chart.png"
    filepath = os.path.join(OUTPUT_DIR, filename)
    
    try:
        page_text = (await _get_body_text(page))[:1000]
        if "403 ERROR" in page_text or "The request could not be satisfied" in page_text:
            raise ValueError(f"TradingView page is blocked: {page_text[:300]}")

        # Always scroll to top first — defends against the page being scrolled
        # to footer for any reason. Without this the viewport snapshot fallback
        # would capture the cookie banner / social-link footer instead of chart.
        await page.evaluate("window.scrollTo(0, 0)")
        await asyncio.sleep(0.2)
        if req.key == "nasdaq":
            # Make the IG symbol page state explicit. The text quote comes from
            # IG:NASDAQ; the image must come from the same symbol overview
            # chart, not TradingView's lower white performance widget.
            nasdaq_viewport = {"width": 1200, "height": 900}
            default_clip = {"x": 0, "y": 490, "width": nasdaq_viewport["width"], "height": 345}
            clip = {
                "x": 0,
                "y": float(req.crop_y) if req.crop_y is not None else default_clip["y"],
                "width": nasdaq_viewport["width"],
                "height": float(req.crop_height) if req.crop_height is not None else default_clip["height"],
            }
            await page.set_viewport_size(nasdaq_viewport)
            await page.goto(CHART_TABS[req.key], wait_until="networkidle", timeout=60000)
            await page.evaluate("window.scrollTo(0, 0)")
            await asyncio.sleep(1)
            clicked = await page.evaluate("""() => {
                const controls = Array.from(document.querySelectorAll('button, a, [role="button"]'));
                const oneDay = controls.find(el => (el.textContent || '').trim() === '1 day');
                if (oneDay) { oneDay.click(); return true; }
                return false;
            }""")
            # Clicking "1 day" re-fetches the intraday series and repaints the
            # chart canvas. A blind sleep races that repaint: when TradingView is
            # slow the screenshot fires before the price line is drawn, so the
            # capture shows the frame + logo but a blank chart (only our PIL
            # title/session overlay survives). Wait for the data fetch to go idle,
            # then give the canvas a moment to finish painting.
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception as wait_exc:
                print(f"  ⚠ NASDAQ networkidle wait after 1-day click skipped: "
                      f"{type(wait_exc).__name__}: {wait_exc}")
            await asyncio.sleep(2 if clicked else 1)
            # Fixed against the IG symbol-page layout after pressing 1 day.
            # Optional crop_* request fields let us tune this live with curl
            # without restarting the Playwright service for every attempt.
            await page.screenshot(
                path=filepath,
                clip=clip,
            )
            print(f"  ✅ NASDAQ IG page snapshot saved: {filename} (clip: y={clip['y']:.0f} h={clip['height']:.0f})")
            meta = CHART_META.get(req.key)
            if meta:
                _trim_bottom_whitespace(filepath)
                _overlay_title(filepath, meta["title"])
            # Trading-session markers (TW + US pre/regular/post in TW time).
            # Overlay failure must not fail an otherwise-valid snapshot.
            try:
                from overlay_market_sessions import overlay_sessions_on_file
                overlay_sessions_on_file(filepath)
                print("  🕒 Trading-session overlay added")
            except Exception as overlay_exc:
                print(f"  ⚠ Session overlay skipped: {type(overlay_exc).__name__}: {overlay_exc}")
            return {"status": "success", "url": filename, "path": filepath, "clip": clip, "viewport": nasdaq_viewport}

        # Clip the chart area. TradingView uses DIFFERENT page templates for
        # different symbol types:
        #   • Forex / equities → "performance-chart-id" container
        #   • Futures (e.g. CBOT_MINI:10Y1!) → no named perf container; main
        #     price chart is a <canvas> inside body, near the top of the page
        # So: try named containers first. Then fall back to the largest
        # CANVAS (not iframe — those are usually ads/social) in the UPPER
        # portion of the page where the price chart actually renders.
        clip = await page.evaluate("""(key) => {
            const preferOverviewChart = key === 'nasdaq';
            const inUpperPage = (r) => r.top >= 40 && r.top <= 600;
            const footerTextPattern = /(Look first\\s*\\/\\s*Then leap|Select market data|©\\s*20\\d{2}\\s*TradingView|Copyright\\s*©)/i;
            const footerLike = Array.from(document.querySelectorAll('footer, [class*="footer"], [class*="Footer"], body *'))
                .map(el => ({el, r: el.getBoundingClientRect(), text: (el.textContent || '').slice(0, 500)}))
                .filter(item => item.r.width > 200 && item.r.height > 40 && footerTextPattern.test(item.text))
                .sort((a, b) => a.r.top - b.r.top)[0];
            const footerTop = footerLike ? footerLike.r.top : window.innerHeight;
            const visibleChartLike = (el, ymax) => {
                const r = el.getBoundingClientRect();
                if (r.width < 250 || r.height < 120) return false;
                if (r.top < 40 || r.top > ymax) return false;
                if ((el.textContent || '').match(footerTextPattern)) return false;
                if (r.top >= footerTop - 20) return false;
                const style = window.getComputedStyle(el);
                return style
                    && style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && Number(style.opacity || 1) > 0;
            };

            if (preferOverviewChart) {
                const overviewCanvases = Array.from(document.querySelectorAll('canvas'))
                    .filter(c => !c.closest('div[data-container-name="performance-chart-id"]'))
                    .filter(c => visibleChartLike(c, 760))
                    .map(c => ({el: c, r: c.getBoundingClientRect()}))
                    .sort((a, b) => (b.r.width * b.r.height) - (a.r.width * a.r.height));
                if (overviewCanvases.length) {
                    const r = overviewCanvases[0].r;
                    const pad = 14;
                    const y = Math.max(0, r.top - pad);
                    const bottom = Math.min(window.innerHeight, footerTop - pad, r.bottom + 70);
                    return {
                        x: Math.max(0, r.left - pad),
                        y,
                        width:  Math.min(window.innerWidth,  r.right  + pad) - Math.max(0, r.left - pad),
                        height: Math.max(180, bottom - y),
                    };
                }
            }

            // 1) Try named containers in priority order — covers forex/equity
            //    AND futures/index page templates.
            //
            // NASDAQ uses the TradingView symbol overview chart for the
            // requested IG-NASDAQ 1D page. The generic performance container
            // can render a long-range chart, so keep it as a fallback only for
            // this key.
            const overviewSelectors = [
                'div[data-container-name="symbol-overview-chart-container"]',
                'div[data-container-name="symbol-page-chart"]',
                'div[data-name="symbol-page-chart-section"]',
                'div[class*="chartContainer"]',
            ];
            const performanceSelectors = [
                'div[data-container-name="performance-chart-id"]',
            ];
            const containerSelectors = preferOverviewChart
                ? overviewSelectors
                : [...performanceSelectors, ...overviewSelectors];
            for (const sel of containerSelectors) {
                const el = document.querySelector(sel);
                if (el && visibleChartLike(el, 800)) {
                const r = el.getBoundingClientRect();
                const pad = 10;
                const y = Math.max(0, r.top - pad);
                const bottom = Math.min(window.innerHeight, footerTop - pad, r.bottom + pad);
                return {
                    x: Math.max(0, r.left - pad),
                    y,
                    width:  Math.min(window.innerWidth,  r.right  + pad) - Math.max(0, r.left - pad),
                    height: Math.max(120, bottom - y),
                };
            }
            }

            // 2) Fallback: largest CANVAS in upper page (futures page case).
            //    Iframes excluded — they're often ads/social widgets that
            //    would steal "largest area" but be unrelated to the chart.
            const canvases = Array.from(document.querySelectorAll('canvas'))
                .filter(c => visibleChartLike(c, 600))
                .map(c => ({el: c, r: c.getBoundingClientRect()}))
                .sort((a, b) => (b.r.width * b.r.height) - (a.r.width * a.r.height));
            if (canvases.length) {
                const r = canvases[0].r;
                const pad = 10;
                const y = Math.max(0, r.top - pad);
                const bottom = Math.min(window.innerHeight, footerTop - pad, r.bottom + pad);
                return {
                    x: Math.max(0, r.left - pad),
                    y,
                    width:  Math.min(window.innerWidth,  r.right  + pad) - Math.max(0, r.left - pad),
                    height: Math.max(120, bottom - y),
                };
            }
            return null;
        }""", req.key)

        if clip and clip["width"] >= 250 and clip["height"] >= 120:
            await page.screenshot(path=filepath, clip=clip)
            print(f"  ✅ Snapshot saved: {filename} (clip: y={clip['y']:.0f} h={clip['height']:.0f})")
        else:
            # Last-resort: take only the UPPER part of the viewport. Bounded
            # rectangle avoids accidentally returning the footer/cookies area
            # (which is what the previous unrestricted full-viewport fallback
            # produced for the bond futures page).
            await page.screenshot(
                path=filepath,
                clip={"x": 0, "y": 40, "width": 600, "height": 460},
            )
            print(f"  ⚠ Snapshot fallback to upper-viewport bounded clip: {filename}")
        
        # --- Overlay Chinese title ---
        meta = CHART_META.get(req.key)
        if meta:
            _trim_bottom_whitespace(filepath)
            _overlay_title(filepath, meta["title"])
        
        return {"status": "success", "url": filename, "path": filepath}
    except Exception as e:
        print(f"❌ Error during snapshot for {req.key}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=5005)
