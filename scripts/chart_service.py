import os
import asyncio
import time
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from playwright.async_api import async_playwright
from PIL import Image, ImageDraw, ImageFont

app = FastAPI()

# Configuration
CHART_TABS = {
    "oil": "TVC:USOIL",
    "bond": "TVC:US10Y",
    "usdtwd": "TVC:USDTWD",
    "usdjpy": "FX:USDJPY",
    "usdchf": "FX:USDCHF"
}
TEMPLATE_PATH = f"file://{os.path.join(os.getcwd(), 'scripts', 'chart_template.html')}"
OUTPUT_DIR = os.path.join(os.getcwd(), 'data', 'images')
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Global State
browser_context = None
browser_instance = None
pages = {}

async def init_browser():
    global browser_instance, browser_context, pages
    print("🚀 Initializing persistent browser instance...")
    p = await async_playwright().start()
    browser_instance = await p.chromium.launch(headless=True)
    browser_context = await browser_instance.new_context(viewport={'width': 1000, 'height': 600})
    
    # Pre-load tabs
    for key, symbol in CHART_TABS.items():
        page = await browser_context.new_page()
        url = f"{TEMPLATE_PATH}?symbol={symbol}"
        print(f"  - Loading tab: {key} ({symbol})")
        await page.goto(url)
        # Wait for the widget to initialize (approx 3s)
        await asyncio.sleep(3)
        pages[key] = page
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
    title: str
    price: str
    change: str
    color: str # hex color for the theme

@app.post("/snapshot")
async def take_snapshot(req: SnapshotRequest):
    if req.key not in pages:
        raise HTTPException(status_code=404, detail="Tab key not found")
    
    page = pages[req.key]
    filename = f"{req.key}_chart.png"
    filepath = os.path.join(OUTPUT_DIR, filename)
    
    try:
        # 1. Take raw screenshot of the chart container
        await page.screenshot(path=filepath)
        
        # 2. Post-process with Pillow (Overlay text)
        with Image.open(filepath) as img:
            draw = ImageDraw.Draw(img)
            # Use a bold font if available, fallback to default
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
            
            # Simple header overlay
            draw.text((20, 20), req.title, font=title_font, fill=req.color)
            draw.text((20, 65), f"{req.price} ({req.change})", font=sub_font, fill="white")
            
            img.save(filepath)
            
        return {"status": "success", "url": filename, "path": filepath}
    except Exception as e:
        print(f"❌ Error during snapshot for {req.key}: {e}")
        # Attempt to recovery: re-init if the page is dead
        await init_browser()
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=5005)
