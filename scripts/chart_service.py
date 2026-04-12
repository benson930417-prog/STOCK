import os
import asyncio
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from playwright.async_api import async_playwright

app = FastAPI()

# Configuration - Using the full URLs provided by the user
CHART_TABS = {
    "oil": "https://www.tradingview.com/symbols/USOIL/?exchange=TVC&timeframe=5D",
    "brent": "https://www.tradingview.com/symbols/RUS-BR1!/?timeframe=5D",
    "bond": "https://www.tradingview.com/symbols/TVC-US10Y/?timeframe=5D",
    "usdtwd": "https://www.tradingview.com/symbols/USDTWD/?exchange=FX_IDC&timeframe=5D",
    "usdjpy": "https://www.tradingview.com/symbols/USDJPY/?exchange=OANDA&timeframe=5D",
    "usdchf": "https://www.tradingview.com/symbols/USDCHF/?exchange=OANDA&timeframe=5D"
}

OUTPUT_DIR = os.path.join(os.getcwd(), 'data', 'images')
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Global State
browser_context = None
browser_instance = None
pages = {}

async def clean_page(page):
    """Hide distracting UI elements, keep only the ticker header + chart."""
    print("  - Cleaning page layout...")
    try:
        await page.add_style_tag(content="""
            /* Hide Cookie & Upgrade Banners */
            div[class*="cookies-banner"], div[class*="cookie-banner"], div[id*="cookies-banner"],
            div[class*="upgrade-button"], div[class*="fixed-banners"], div[class*="toast-notif"] { display: none !important; }
            
            /* Hide Website Header/Nav */
            header, #header-container, .tv-header, div[class*="layout__header"] { display: none !important; }

            /* Hide Sidebars */
            aside, .tv-side-toolbar { display: none !important; }

            /* Hide the sticky top ticker bar (duplicate of main ticker) */
            div[class*="pageHead-"] { display: none !important; }

            /* Hide breadcrumbs navigation */
            nav[aria-label="Breadcrumbs"] { display: none !important; }

            /* Hide the exchange/contract selector row (Continuous contract, BR1!, Russian Exchange, etc.) */
            div[class*="buttonsRow-"], div[class*="quotesRow-"] { display: none !important; }

            /* Hide Tab Bar (Overview, News, Community, Technicals, etc.) */
            div[class*="tabsRow-"], div[class*="tabs-"] { display: none !important; }

            /* Hide chart action buttons (Full chart, Snapshot, Get widget, Embed) */
            a[aria-label="Full chart"], button[aria-label="Take a snapshot"], a[aria-label="Get widget"],
            a[class*="containerLink-"] { display: none !important; }

            /* Hide range selector buttons (1D, 5D, 1M, etc.) */
            div:has(> button[class*="rangeButton-"]) { display: none !important; }

            /* Hide the "Chart >" section header above the chart */
            div[class*="sectionTitle-"] { display: none !important; }

            /* Hide the TradingView Watermark Logo (aggressive) */
            a[class*="label__link-"], div[class*="branding"],
            span[class*="brand"], a[href*="tradingview.com"][class*="label"] { display: none !important; }

            /* Hide quotes subtitle line (e.g. "As of today at ...") */
            div[class*="quotesSubLine-"] { display: none !important; }

            /* Hide EVERYTHING below the chart (Contract highlights, etc.) */
            div[data-container-name="performance-chart-id"] ~ * { display: none !important; }

            /* Trim chart container margins */
            div[data-container-name="performance-chart-id"] { 
                padding-bottom: 0 !important; 
                margin-bottom: 0 !important; 
                border: none !important;
            }
        """)
        await asyncio.sleep(2)
    except Exception as e:
        print(f"    - Page cleaning warning: {e}")

async def init_browser():
    global browser_instance, browser_context, pages
    print("🚀 Initializing Browser (Always-Open tabs)...")
    p = await async_playwright().start()
    browser_instance = await p.chromium.launch(headless=True)
    browser_context = await browser_instance.new_context(
        viewport={'width': 1200, 'height': 550},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        timezone_id="Asia/Taipei"
    )
    
    for key, url in CHART_TABS.items():
        print(f"  - Loading tab: {key}")
        page = await browser_context.new_page()
        try:
            await page.goto(url, wait_until="networkidle", timeout=60000)
            await clean_page(page)
            pages[key] = page
        except Exception as e:
            print(f"❌ Failed to load {key}: {e}")

    print("✅ All tabs pre-loaded and ready.")

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
        # Pages are pre-loaded at startup and TradingView updates live via WebSocket.
        # No reload needed — just scroll and screenshot instantly.
        chart_selector = 'div[data-container-name="performance-chart-id"]'
        chart_el = page.locator(chart_selector)
        await chart_el.scroll_into_view_if_needed()
        
        # Viewport screenshot — ticker header + chart, junk hidden by CSS.
        await page.screenshot(path=filepath, full_page=False)
        
        print(f"  ✅ Snapshot saved: {filename}")
        return {"status": "success", "url": filename, "path": filepath}
    except Exception as e:
        print(f"❌ Error during snapshot for {req.key}: {e}")
        await init_browser()
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=5005)
