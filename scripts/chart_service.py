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

            /* Hide the sticky top ticker bar */
            div[class*="pageHead-"] { display: none !important; }

            /* Hide Breadcrumbs */
            nav[aria-label="Breadcrumbs"], div[class*="breadcrumb"] { display: none !important; }

            /* Hide the exchange/contract selector row */
            div[class*="buttonsRow-"], div[class*="quotesRow-"] { display: none !important; }

            /* Hide Tab Bar (Overview, News, etc.) */
            div[class*="tabsRow-"], div[class*="tabs-"], div[role="tablist"] { display: none !important; }

            /* Hide chart action buttons */
            a[aria-label="Full chart"], button[aria-label="Take a snapshot"], a[aria-label="Get widget"],
            a[class*="containerLink-"] { display: none !important; }

            /* Hide range selector buttons (1D, 5D, etc.) */
            div:has(> button[class*="rangeButton-"]) { display: none !important; }

            /* Hide "Chart >" section header */
            div[class*="sectionTitle-"] { display: none !important; }

            /* Hide TradingView Watermark Logo (desktop + mobile) */
            a[class*="label__link-"], div[class*="branding"],
            span[class*="brand"], a[href*="tradingview.com"][class*="label"],
            div[class*="tradingview-widget"], div[class*="tv-logo"],
            a[class*="logo"], img[alt*="TradingView"],
            div[class*="watermark"], span[class*="watermark"] { display: none !important; }

            /* Hide notification / settings icons that appear at narrow widths */
            button[class*="notification"], div[class*="notification"],
            button[aria-label*="notification"], button[aria-label*="Notification"],
            div[class*="rightGroup-"], div[class*="actionIcons-"],
            button[class*="iconButton-"], a[class*="iconButton-"] { display: none !important; }

            /* Nuclear: hide any stray SVG logos / icons in the header area */
            #perfect-capture-wrapper svg { max-height: 20px; }

            /* Hide quotes subtitle line */
            div[class*="quotesSubLine-"] { display: none !important; }

            /* Hide EVERYTHING below the chart */
            div[data-container-name="performance-chart-id"] ~ * { display: none !important; }

            /* Remove chart container internal margins */
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
        viewport={'width': 600, 'height': 550},
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
        print(f"  - Preparing perfect capture for {req.key}...")
        
        # Inject JS to wrap header + chart into a perfect container
        await page.evaluate("""() => {
            // --- 1. Nuke stray elements from the ENTIRE page first ---
            // Remove all TradingView logo/watermark elements
            document.querySelectorAll([
                'a[class*="logo"]', 'div[class*="logo"]', 'img[alt*="TradingView"]',
                'div[class*="branding"]', 'a[class*="label__link"]',
                'div[class*="watermark"]', 'span[class*="watermark"]',
                'div[class*="tv-logo"]'
            ].join(',')).forEach(el => el.remove());
            
            // Remove notification / action icons
            document.querySelectorAll([
                'button[class*="notification"]', 'div[class*="notification"]',
                'button[class*="iconButton"]', 'a[class*="iconButton"]',
                'div[class*="rightGroup"]', 'div[class*="actionIcons"]'
            ].join(',')).forEach(el => el.remove());
            
            // --- 2. Find header and chart ---
            const header = document.querySelector('div[class*="symbol-header-container"]') || 
                           document.querySelector('div[id*="symbol-header"]') ||
                           document.querySelector('h1')?.parentElement;
            
            const chart = document.querySelector('div[data-container-name="performance-chart-id"]');
            
            if (!header || !chart) {
                console.error("Perfect Capture Error: Header or Chart not found", {header, chart});
                return;
            }
            
            // --- 3. Clean the header: remove any stray SVGs/links that aren't part of the symbol info ---
            // Find all siblings of the header inside its parent and remove non-header junk
            const headerParent = header.parentElement;
            if (headerParent) {
                Array.from(headerParent.children).forEach(child => {
                    if (child !== header && child !== chart && !child.querySelector('h1') && 
                        !child.textContent.includes('USD') && !child.textContent.includes('%')) {
                        child.style.display = 'none';
                    }
                });
            }
            
            // Remove stray SVGs inside the header that are bigger logos (not small indicator icons)
            header.querySelectorAll('svg').forEach(svg => {
                const rect = svg.getBoundingClientRect();
                if (rect.width > 30 || rect.height > 30) {
                    svg.remove();
                }
            });
            
            // Remove any <a> tags that link to tradingview.com (logo links)
            header.querySelectorAll('a[href*="tradingview.com"]').forEach(el => el.remove());
            
            // --- 4. Remove watermark from inside the chart canvas area ---
            chart.querySelectorAll([
                'a[class*="label__link"]', 'div[class*="branding"]',
                'span[class*="brand"]', 'a[href*="tradingview.com"]',
                'div[class*="watermark"]'
            ].join(',')).forEach(el => el.remove());
            
            // --- 5. Set up spacing ---
            header.style.setProperty('margin-bottom', '0', 'important');
            header.style.setProperty('padding-bottom', '5px', 'important');
            chart.style.setProperty('margin-top', '0', 'important');
            chart.style.setProperty('padding-top', '0', 'important');
            
            // --- 6. Create wrapper if not already exists ---
            let wrapper = document.getElementById('perfect-capture-wrapper');
            if (!wrapper) {
                wrapper = document.createElement('div');
                wrapper.id = 'perfect-capture-wrapper';
                wrapper.style.padding = '15px';
                wrapper.style.backgroundColor = 'white';
                wrapper.style.display = 'inline-block';
                wrapper.style.width = '100%';
                wrapper.style.boxSizing = 'border-box';
                wrapper.style.overflow = 'hidden';
                
                header.parentNode.insertBefore(wrapper, header);
                wrapper.appendChild(header);
                wrapper.appendChild(chart);
            }
            
            // --- 7. Force dark text on white background ---
            header.querySelectorAll('*').forEach(el => {
                const style = window.getComputedStyle(el);
                if (style.color === 'rgb(255, 255, 255)' || style.color === 'white') {
                    el.style.setProperty('color', '#131722', 'important');
                }
            });
        }""")
        
        # Target the specifically created wrapper
        target_selector = '#perfect-capture-wrapper'
        target_el = page.locator(target_selector)
        
        # Take the screenshot of just that element
        await target_el.screenshot(path=filepath)
        
        print(f"  ✅ Perfect Snapshot saved: {filename}")
        return {"status": "success", "url": filename, "path": filepath}
    except Exception as e:
        print(f"❌ Error during snapshot for {req.key}: {e}")
        await init_browser()
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=5005)
