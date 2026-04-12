import os
import asyncio
from io import BytesIO
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from playwright.async_api import async_playwright
from PIL import Image

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

# Crop settings (pixels to trim from each edge)
CROP_TOP = 0
CROP_BOTTOM = 40   # remove TradingView watermark
CROP_LEFT = 0
CROP_RIGHT = 0

OUTPUT_DIR = os.path.join(os.getcwd(), 'data', 'images')
os.makedirs(OUTPUT_DIR, exist_ok=True)

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
    div[data-container-name="performance-chart-id"] ~ * {
        display: none !important;
    }
    body { overflow: hidden !important; }
"""

async def init_browser():
    global browser_instance, browser_context, pages
    print("🚀 Initializing Browser...")
    p = await async_playwright().start()
    browser_instance = await p.chromium.launch(headless=True)
    browser_context = await browser_instance.new_context(
        viewport={'width': 600, 'height': 700},
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
        # Find the chart element and screenshot it directly
        chart = page.locator('div[data-container-name="performance-chart-id"]')
        raw_bytes = await chart.screenshot()
        
        # Crop with Pillow to remove watermark / edges
        img = Image.open(BytesIO(raw_bytes))
        w, h = img.size
        cropped = img.crop((
            CROP_LEFT,
            CROP_TOP,
            w - CROP_RIGHT,
            h - CROP_BOTTOM
        ))
        cropped.save(filepath)
        
        print(f"  ✅ Snapshot saved: {filename} ({cropped.size[0]}x{cropped.size[1]})")
        return {"status": "success", "url": filename, "path": filepath}
    except Exception as e:
        print(f"❌ Error during snapshot for {req.key}: {e}")
        await init_browser()
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=5005)
