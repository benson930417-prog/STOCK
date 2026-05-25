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
    "bond": "https://www.tradingview.com/symbols/CBOT_MINI-10Y1!/?timeframe=5D",
    "gold": "https://www.tradingview.com/symbols/GOLD/?timeframe=5D",
    "usdtwd": "https://www.tradingview.com/symbols/FX_IDC-USDTWD/?timeframe=5D",
    "usdjpy": "https://www.tradingview.com/symbols/OANDA-USDJPY/?timeframe=5D",
    "usdchf": "https://www.tradingview.com/symbols/OANDA-USDCHF/?timeframe=5D"
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
    "usdtwd": {"scanner": "forex", "symbol": "FX_IDC:USDTWD"},
    "usdjpy": {"scanner": "forex", "symbol": "OANDA:USDJPY"},
    "usdchf": {"scanner": "forex", "symbol": "OANDA:USDCHF"},
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


async def _fetch_tradingview_scanner_quote(page, key):
    config = TRADINGVIEW_SCANNER_QUOTES.get(key)
    if not config:
        return None
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
    labels = [
        ("1d", "1日："),
        ("5d", "1週："),
        ("1m", "1月："),
        ("6m", "6月："),
    ]
    lines = [
        f"{emoji} {title}".strip(),
        "──────────",
        f"🕒 最新報價：{price:,.{precision}f} {unit}",
        "",
        "📊 近期漲跌幅：",
    ]
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
        quote = await _fetch_tradingview_scanner_quote(page, req.key)
        if quote is None:
            try:
                quote = await _extract_market_quote(page)
            except Exception as dom_error:
                print(f"⚠️ DOM quote extraction failed for {req.key}: {dom_error}")
                text = await _get_body_text(page)
                quote = _parse_market_text(text)
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

        # Clip ONLY the chart canvas area (no header). Different TradingView
        # symbol pages use different container names, so fall back to visible
        # chart-like elements when the performance-chart wrapper is absent.
        clip = await page.evaluate("""() => {
            const chartContainer = document.querySelector('div[data-container-name="performance-chart-id"]');
            const visibleChartLike = (el) => {
                const r = el.getBoundingClientRect();
                if (r.width < 250 || r.height < 120 || r.top < 40 || r.top > 700) return false;
                const style = window.getComputedStyle(el);
                return style && style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || 1) > 0;
            };
            const graphicElements = chartContainer
                ? Array.from(chartContainer.querySelectorAll('canvas, svg, iframe')).filter(visibleChartLike)
                : Array.from(document.querySelectorAll('canvas, svg, iframe')).filter(visibleChartLike);
            const containerElements = chartContainer
                ? [chartContainer]
                : Array.from(document.querySelectorAll([
                    'div[class*="chart"]',
                    'div[class*="Chart"]',
                    'div[data-name*="chart"]',
                    'div[data-name*="Chart"]'
                ].join(','))).filter(visibleChartLike);
            const elements = graphicElements.length ? graphicElements : containerElements;
            const rects = elements
                .map(el => el.getBoundingClientRect())
                .filter(r => r.width > 250 && r.height > 120 && r.top > 40 && r.top < 700);
            if (!rects.length) return null;

            const bestRect = chartContainer
                ? chartContainer.getBoundingClientRect()
                : rects.sort((a, b) => (b.width * b.height) - (a.width * a.height))[0];
            const pad = 10;
            const top = Math.max(0, bestRect.top - pad);
            const bottom = Math.min(window.innerHeight, bestRect.bottom + pad);
            const left = Math.max(0, bestRect.left - pad);
            const right = Math.min(window.innerWidth, bestRect.right + pad);
            
            return {
                x: left,
                y: top,
                width: Math.max(250, right - left),
                height: bottom - top
            };
        }""")
        
        if clip:
            await page.screenshot(path=filepath, clip=clip)
            print(f"  ✅ Snapshot saved: {filename} (clip: y={clip['y']:.0f} h={clip['height']:.0f})")
        else:
            raise ValueError("TradingView performance chart container was not found")
        
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
