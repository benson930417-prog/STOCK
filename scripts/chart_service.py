import os
import asyncio
import time
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from playwright.async_api import async_playwright
from PIL import Image, ImageDraw, ImageFont

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
    """Hide distracting UI elements from the TradingView main site."""
    print("  - Cleaning up page elements...")
    try:
        # 1. Dismiss/Hide Cookie Banners
        await page.add_style_tag(content="""
            div[class*="cookies-banner"], 
            div[class*="cookie-banner"], 
            div[id*="cookies-banner"] { display: none !important; }
            
            /* Hide the main site headers and sidebars */
            header, 
            #header-container, 
            .tv-header, 
            div[class*="layout__header"],
            aside,
            .tv-side-toolbar { display: none !important; }

            /* Hide 'Upgrade' and 'Pricing' banners */
            div[class*="upgrade-button"], 
            div[class*="fixed-banners"],
            div[class*="toast-notif"] { display: none !important; }

            /* Hide extra chart UI elements requested by user */
            a[aria-label="Full chart"],
            button[aria-label="Take a snapshot"],
            a[aria-label="Get widget"],
            a[class*="containerLink-"],
            a[aria-label*="TradingView"][class*="label__link"],
            div:has(> button[class*="rangeButton-"]) { display: none !important; }

            /* Hide bottom 'Ads' or disclaimer area if possible */
            div[class*="disclaimer"] { display: none !important; }
            
            /* Ensure the chart area is visible */
            div[data-container-name="performance-chart-id"] { border: none !important; }
        """)
        # Wait a moment for stable layout
        await asyncio.sleep(1)
    except Exception as e:
        print(f"    - Page cleaning warning: {e}")

async def init_browser():
    global browser_instance, browser_context, pages
    print("🚀 Initializing Main Site Automation (Always-Open)...")
    p = await async_playwright().start()
    browser_instance = await p.chromium.launch(headless=True)
    browser_context = await browser_instance.new_context(
        viewport={'width': 1200, 'height': 800},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    
    for key, url in CHART_TABS.items():
        print(f"  - Loading tab: {key}")
        page = await browser_context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(5) # Allow heavy JS to settle
            await clean_page(page)
            pages[key] = page
        except Exception as e:
            print(f"❌ Failed to load {url}: {e}")

    print("✅ All site tabs pre-loaded and ready.")

@app.on_event("startup")
async def startup_event():
    await init_browser()

@app.on_event("shutdown")
async def shutdown_event():
    if browser_instance:
        await browser_instance.close()

class SnapshotRequest(BaseModel):
    key: str
    title: str
    price: str
    change: str
    color: str

@app.post("/snapshot")
async def take_snapshot(req: SnapshotRequest):
    if req.key not in pages:
        # Fallback: attempt to reload
        await init_browser()
        if req.key not in pages:
            raise HTTPException(status_code=404, detail="Tab key not found")
    
    page = pages[req.key]
    filename = f"{req.key}_chart.png"
    filepath = os.path.join(OUTPUT_DIR, filename)
    
    try:
        # Focus the element identified by the user
        selector = 'div[data-container-name="performance-chart-id"]'
        chart_element = page.locator(selector)
        
        # Ensure it's in view
        await chart_element.scroll_into_view_if_needed()
        
        # Snapshot just the element
        await chart_element.screenshot(path=filepath)
        
        # Post-process with Pillow (Overlay text)
        with Image.open(filepath) as img:
            draw = ImageDraw.Draw(img)
            try:
                # Path for Ubuntu default fonts or local fonts
                font_path = "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"
                if not os.path.exists(font_path):
                    font_path = os.path.join(os.getcwd(), 'data', 'fonts', 'NotoSansTC-Regular.otf')
                
                title_font = ImageFont.truetype(font_path, 32)
                sub_font = ImageFont.truetype(font_path, 20)
            except:
                title_font = ImageFont.load_default()
                sub_font = ImageFont.load_default()
            
            # Header overlay
            draw.text((20, 20), req.title, font=title_font, fill=req.color)
            draw.text((20, 65), f"{req.price} ({req.change})", font=sub_font, fill="white")
            
            img.save(filepath)
            
        return {"status": "success", "url": filename, "path": filepath}
    except Exception as e:
        print(f"❌ Error during snapshot for {req.key}: {e}")
        # Soft restart browser if things are clearly broken
        if "page.screenshot" in str(e):
            await init_browser()
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=5005)
