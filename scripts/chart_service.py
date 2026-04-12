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
    """Hide distracting UI elements for a clean chart screenshot."""
    print("  - Applying Pro-Aesthetics (Light Mode & Layout)...")
    try:
        # Add Styles for a clean, tight look
        await page.add_style_tag(content="""
            /* Hide Cookie & Upgrade Banners */
            div[class*="cookies-banner"], div[class*="cookie-banner"], div[id*="cookies-banner"],
            div[class*="upgrade-button"], div[class*="fixed-banners"], div[class*="toast-notif"] { display: none !important; }
            
            /* Hide Website Headers/Sidebars */
            header, #header-container, .tv-header, div[class*="layout__header"], aside, .tv-side-toolbar { display: none !important; }

            /* Hide extra chart UI elements (Buttons, Embeds, Titles) */
            a[aria-label="Full chart"], button[aria-label="Take a snapshot"], a[aria-label="Get widget"],
            a[class*="containerLink-"], div:has(> button[class*="rangeButton-"]) { display: none !important; }

            /* CRITICAL: Hide the TradingView Watermark Logo */
            a[class*="label__link-"], div[class*="branding"] { display: none !important; }

            /* ELIMINATE BOTTOM WHITE SPACE: Trim the container margins */
            div[data-container-name="performance-chart-id"] { 
                padding-bottom: 0 !important; 
                margin-bottom: 0 !important; 
                border: none !important;
            }
        """)
        await asyncio.sleep(2) # Allow styles to settle
    except Exception as e:
        print(f"    - Page cleaning warning: {e}")

async def init_browser():
    global browser_instance, browser_context, pages
    print("🚀 Initializing Main Site Automation (Always-Open)...")
    p = await async_playwright().start()
    browser_instance = await p.chromium.launch(headless=True)
    browser_context = await browser_instance.new_context(
        viewport={'width': 1200, 'height': 800},
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

    print("✅ All site tabs pre-loaded and ready.")

@app.on_event("startup")
async def startup_event():
    await init_browser()

@app.on_event("shutdown")
async def shutdown_event():
    if browser_instance:
        await browser_instance.close()

async def scrape_ticker_data(page):
    """Scrape title, price, change, and color directly from TradingView page DOM."""
    data = {"title": "", "price": "", "change": "", "color": "#333333"}
    try:
        # Title from h1
        h1 = page.locator('h1').first
        data["title"] = (await h1.text_content()).strip() if await h1.count() else ""
        
        # Price from the dedicated data-qa-id element
        price_el = page.locator('span[data-qa-id="symbol-last-value"]').first
        data["price"] = (await price_el.text_content()).strip() if await price_el.count() else ""
        
        # Change values (absolute + percentage) from js-symbol-change-pt spans
        change_spans = page.locator('span.js-symbol-change-pt')
        change_count = await change_spans.count()
        change_parts = []
        for i in range(change_count):
            txt = (await change_spans.nth(i).text_content()).strip()
            if txt:
                change_parts.append(txt)
        data["change"] = "  ".join(change_parts)  # e.g. "+0.034  +0.79%"
        
        # Color from change direction container class (up = green, down = red)
        direction_div = page.locator('div.js-symbol-change-direction').first
        if await direction_div.count():
            cls = await direction_div.get_attribute('class') or ""
            if 'up' in cls.lower().split('-')[0] or any(part.startswith('up') for part in cls.split()):
                # Check for 'up-' prefix in any class token
                data["color"] = "#089981"  # TradingView green
            else:
                data["color"] = "#F23645"  # TradingView red
            # More robust: check if any class token starts with 'up'
            tokens = cls.split()
            is_up = any(t.startswith('up-') or t == 'up' for t in tokens)
            data["color"] = "#089981" if is_up else "#F23645"
    except Exception as e:
        print(f"    - Ticker scrape warning: {e}")
    return data

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
        # Reload the page to get fresh data
        await page.reload(wait_until="networkidle", timeout=60000)
        await clean_page(page)
        
        # Scrape live ticker data directly from TradingView DOM
        ticker = await scrape_ticker_data(page)
        print(f"  - Scraped [{req.key}]: {ticker['title']} | {ticker['price']} | {ticker['change']} | {ticker['color']}")
        
        selector = 'div[data-container-name="performance-chart-id"]'
        chart_element = page.locator(selector)
        await chart_element.scroll_into_view_if_needed()
        
        # Snapshot the element
        await chart_element.screenshot(path=filepath)
        
        # POST-PROCESS: Add a dedicated Header Section using Pillow
        with Image.open(filepath) as chart_img:
            cw, ch = chart_img.size
            header_h = 100
            
            # Create a new canvas with room for the header
            # Light mode header background
            final_img = Image.new('RGB', (cw, ch + header_h), color='#F0F0F0')
            final_img.paste(chart_img, (0, header_h))
            
            draw = ImageDraw.Draw(final_img)
            # Add a subtle separator line
            draw.line([(0, header_h), (cw, header_h)], fill="#CCCCCC", width=1)
            
            try:
                font_path = "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"
                if not os.path.exists(font_path):
                    font_path = os.path.join(os.getcwd(), 'data', 'fonts', 'NotoSansTC-Regular.otf')
                
                title_font = ImageFont.truetype(font_path, 34)
                price_font = ImageFont.truetype(font_path, 24)
            except:
                title_font = ImageFont.load_default()
                price_font = ImageFont.load_default()
            
            # Draw Title and Price in the Header area (from TradingView)
            draw.text((25, 15), ticker["title"], font=title_font, fill='#1a1a1a')
            draw.text((25, 58), f"{ticker['price']}  ", font=price_font, fill='#333333')
            
            # Draw Change (color coded from TradingView direction)
            p_width = draw.textlength(f"{ticker['price']}  ", font=price_font)
            draw.text((25 + p_width, 58), ticker["change"], font=price_font, fill=ticker["color"])
            
            final_img.save(filepath)
            
        return {"status": "success", "url": filename, "path": filepath}
    except Exception as e:
        print(f"❌ Error during snapshot for {req.key}: {e}")
        await init_browser()
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=5005)
