import os
import asyncio
import time
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from playwright.async_api import async_playwright
from PIL import Image, ImageDraw, ImageFont

app = FastAPI()

# Configuration: Use Full TradingView Symbol Pages (Higher Fidelity)
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
    """Hide distracting UI elements to isolate the chart."""
    try:
        await page.evaluate("""() => {
            const selectors = [
                'header', 'footer', '[id*="overlap-manager"]', 
                '[class*="sidebar"]', '[class*="floating"]', 
                '[class*="banner"]', '[class*="newsSection"]',
                '[class*="social"]', '[class*="tv-header"]'
            ];
            selectors.forEach(s => {
                document.querySelectorAll(s).forEach(el => el.style.display = 'none');
            });
            // Force the main container to be visible and clear
            const main = document.querySelector('main');
            if(main) main.style.marginTop = '0';
        }""")
    except:
        pass

async def init_browser():
    global browser_instance, browser_context, pages
    print("🚀 Initializing Full-Site High-Fidelity browser instance...")
    p = await async_playwright().start()
    # Use a large enough viewport to capture the overview chart cleanly
    browser_instance = await p.chromium.launch(headless=True)
    browser_context = await browser_instance.new_context(viewport={'width': 1200, 'height': 800})
    
    for key, url in CHART_TABS.items():
        page = await browser_context.new_page()
        print(f"  - Loading main site tab: {key}")
        try:
            # Wait for network idle as the main site is heavy with JS
            await page.goto(url, wait_until="networkidle", timeout=60000)
            await clean_page(page)
            pages[key] = page
        except Exception as e:
            print(f"  ❌ Failed to load {url}: {e}")
            
    print("✅ All high-fidelity tabs pre-loaded.")

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
        raise HTTPException(status_code=404, detail=f"Tab key '{req.key}' not found or failed to load")
    
    page = pages[req.key]
    filename = f"{req.key}_chart.png"
    filepath = os.path.join(OUTPUT_DIR, filename)
    
    try:
        # Re-clean page in case of dynamic ads or popups appearing
        await clean_page(page)
        
        # Identify the chart container using a robust partial class match
        # TradingView symbol overview uses a 'chartContainer' or similar inside the first section
        chart_element = await page.query_selector('div[class*="chartContainer-"]')
        if not chart_element:
            # Fallback to the main overview area if specific container isn't found
            chart_element = await page.query_selector('section[class*="overview-"]') 
            
        if not chart_element:
            # Absolute fallback: just screenshot the top half of the page
            await page.screenshot(path=filepath, clip={'x': 0, 'y': 0, 'width': 1200, 'height': 500})
        else:
            await chart_element.screenshot(path=filepath)
        
        # 2. Text Overlay with Pillow
        with Image.open(filepath) as img:
            draw = ImageDraw.Draw(img)
            try:
                # Path for Ubuntu default fonts
                font_path = "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"
                if not os.path.exists(font_path):
                    font_path = os.path.join(os.getcwd(), 'data', 'fonts', 'NotoSansTC-Regular.otf')
                title_font = ImageFont.truetype(font_path, 32)
                sub_font = ImageFont.truetype(font_path, 20)
            except:
                title_font = ImageFont.load_default()
                sub_font = ImageFont.load_default()
                
            # Stylish overlay at the top left
            draw.text((30, 30), req.title, font=title_font, fill=req.color)
            draw.text((30, 80), f"{req.price} ({req.change})", font=sub_font, fill="white")
            img.save(filepath)
            
        return {"status": "success", "url": filename, "path": filepath}
    except Exception as e:
        print(f"❌ Snapshot failure for {req.key}: {e}")
        # Consider re-init on major failure
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=5005)
