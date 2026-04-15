import os
import asyncio
import urllib.request
import requests as http_requests
from io import BytesIO
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from playwright.async_api import async_playwright
from PIL import Image, ImageDraw, ImageFont

app = FastAPI()

# Configuration
CHART_TABS = {
    "oil": "https://www.tradingview.com/symbols/USOIL/?exchange=TVC&timeframe=5D",
    "brent": "https://www.tradingview.com/symbols/RUS-BR1!/?timeframe=5D",
    "bond": "https://www.tradingview.com/symbols/TVC-US10Y/?timeframe=5D",
    "usdtwd": "https://www.tradingview.com/symbols/USDTWD/?exchange=FX_IDC&timeframe=5D",
    "usdjpy": "https://www.tradingview.com/symbols/USDJPY/?exchange=OANDA&timeframe=5D",
    "usdchf": "https://www.tradingview.com/symbols/USDCHF/?exchange=OANDA&timeframe=5D"
}

# Chinese titles + Yahoo symbols for each chart key
CHART_META = {
    "oil":    {"title": "WTI 輕原油",   "yahoo": "CL=F",  "precision": 2},
    "brent":  {"title": "布蘭特原油",    "yahoo": "BZ=F",  "precision": 2},
    "bond":   {"title": "10年期公債殖利率", "yahoo": "^TNX", "precision": 3},
    "usdtwd": {"title": "美元兌台幣",    "yahoo": "TWD=X", "precision": 3},
    "usdjpy": {"title": "美元兌日幣",    "yahoo": "JPY=X", "precision": 2},
    "usdchf": {"title": "美元兌瑞郎",    "yahoo": "CHF=X", "precision": 4},
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
    div[data-container-name="performance-chart-id"] a[href^="/chart/"],
    div[data-container-name="performance-chart-id"] ~ *,
    div[class*="symbolHeader-"], div[class*="symbol-header"] {
        display: none !important;
    }
    body { overflow: hidden !important; }
"""


def _fetch_yahoo_price(yahoo_symbol, precision):
    """Fetch latest price and daily change from Yahoo Finance."""
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        r = http_requests.get(
            f'https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol}?range=5d&interval=1d',
            headers=headers, timeout=5
        )
        if r.status_code == 200:
            data = r.json()
            result = data['chart']['result'][0]
            meta = result['meta']
            price = meta['regularMarketPrice']

            timestamps = result['timestamp']
            closes = result['indicators']['quote'][0]['close']
            valid = [c for c in closes if c is not None]

            old_price = valid[-2] if len(valid) >= 2 else price
            change = price - old_price
            change_pct = (change / old_price) * 100

            fmt = f"{{:.{precision}f}}"
            sign = "+" if change_pct >= 0 else ""
            return {
                "price": fmt.format(price),
                "change_pct": f"{sign}{change_pct:.2f}%",
                "is_up": change_pct >= 0,
            }
    except Exception as e:
        print(f"⚠️ Yahoo fetch failed for {yahoo_symbol}: {e}")
    return None


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


async def init_browser():
    global browser_instance, browser_context, pages
    print("🚀 Initializing Browser...")
    p = await async_playwright().start()
    browser_instance = await p.chromium.launch(headless=True)
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
    if browser_instance:
        await browser_instance.close()

class SnapshotRequest(BaseModel):
    key: str

@app.post("/snapshot")
async def take_snapshot(req: SnapshotRequest):
    if req.key not in pages:
        await init_browser()
        if req.key not in pages:
            raise HTTPException(status_code=404, detail="Tab key not found")
    
    page = pages[req.key]
    filename = f"{req.key}_chart.png"
    filepath = os.path.join(OUTPUT_DIR, filename)
    
    try:
        # Clip ONLY the chart canvas area (no header)
        clip = await page.evaluate("""() => {
            const chartContainer = document.querySelector('div[data-container-name="performance-chart-id"]');
            if (!chartContainer) return null;
            
            const canvases = chartContainer.querySelectorAll('canvas');
            let canvasBottom = 0;
            canvases.forEach(c => {
                const r = c.getBoundingClientRect();
                if (r.bottom > canvasBottom) canvasBottom = r.bottom;
            });
            
            if (canvasBottom === 0) {
                canvasBottom = chartContainer.getBoundingClientRect().bottom - 50;
            }
            
            const containerRect = chartContainer.getBoundingClientRect();
            const pad = 10;
            const top = Math.max(0, containerRect.top - pad);
            const bottom = canvasBottom + pad;
            
            return {
                x: 0,
                y: top,
                width: 600,
                height: bottom - top
            };
        }""")
        
        if clip:
            await page.screenshot(path=filepath, clip=clip)
            print(f"  ✅ Snapshot saved: {filename} (clip: y={clip['y']:.0f} h={clip['height']:.0f})")
        else:
            # Fallback: just screenshot viewport
            await page.screenshot(path=filepath)
            print(f"  ⚠️ Fallback screenshot saved: {filename}")
        
        # --- Overlay Chinese title ---
        meta = CHART_META.get(req.key)
        if meta:
            _overlay_title(filepath, meta["title"])
        
        return {"status": "success", "url": filename, "path": filepath}
    except Exception as e:
        print(f"❌ Error during snapshot for {req.key}: {e}")
        await init_browser()
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=5005)
