import os
import asyncio
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

OUTPUT_DIR = os.path.join(os.getcwd(), 'data', 'images')
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Global State
browser_context = None
browser_instance = None
pages = {}

HIDE_CSS = """
    body { overflow: hidden !important; }
"""

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

def add_title(img: Image.Image, title: str) -> Image.Image:
    """Add a title bar above the chart image."""
    title_height = 50
    padding = 12
    
    # Create new image with space for title
    new_img = Image.new('RGB', (img.width, img.height + title_height), 'white')
    new_img.paste(img, (0, title_height))
    
    draw = ImageDraw.Draw(new_img)
    
    # Try to use a nice font, fall back to default
    try:
        font = ImageFont.truetype("arial.ttf", 20)
    except OSError:
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
        except OSError:
            font = ImageFont.load_default()
    
    draw.text((padding, padding), title, fill='#131722', font=font)
    return new_img

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
        # JS: get canvas bounds + title text
        info = await page.evaluate("""() => {
            // 1. Find chart container and its canvases
            const chartContainer = document.querySelector('div[data-container-name="performance-chart-id"]');
            if (!chartContainer) return null;
            
            const canvases = chartContainer.querySelectorAll('canvas');
            let canvasTop = Infinity, canvasBottom = 0;
            canvases.forEach(c => {
                const r = c.getBoundingClientRect();
                if (r.top < canvasTop) canvasTop = r.top;
                if (r.bottom > canvasBottom) canvasBottom = r.bottom;
            });
            
            if (canvasTop === Infinity) return null;
            
            // 2. Extract title text from symbol header
            const h1 = document.querySelector('h1');
            let title = '';
            if (h1) {
                // Get symbol name from h1
                const symbolName = h1.textContent.trim();
                
                // Find the price/change info near the header
                const headerContainer = h1.closest('div[class*="symbolHeader"]') || 
                                        h1.closest('div[class*="symbol-header"]') ||
                                        h1.parentElement?.parentElement;
                if (headerContainer) {
                    title = headerContainer.textContent.trim().replace(/\\s+/g, ' ');
                } else {
                    title = symbolName;
                }
            }
            
            return {
                clip: {
                    x: 0,
                    y: canvasTop,
                    width: 600,
                    height: canvasBottom - canvasTop
                },
                title: title
            };
        }""")
        
        if info and info['clip']:
            # Screenshot just the canvas area
            raw_bytes = await page.screenshot(clip=info['clip'])
            img = Image.open(BytesIO(raw_bytes))
            
            # Overlay title on top
            title = info.get('title', req.key.upper())
            if title:
                img = add_title(img, title)
            
            img.save(filepath)
            print(f"  ✅ Snapshot saved: {filename} ({img.size[0]}x{img.size[1]}) title='{title[:50]}'")
        else:
            await page.screenshot(path=filepath)
            print(f"  ⚠️ Fallback screenshot: {filename}")
        
        return {"status": "success", "url": filename, "path": filepath}
    except Exception as e:
        print(f"❌ Error during snapshot for {req.key}: {e}")
        await init_browser()
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=5005)
