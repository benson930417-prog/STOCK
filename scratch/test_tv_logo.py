import asyncio
from playwright.async_api import async_playwright
import json

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width': 600, 'height': 800})
        page = await context.new_page()
        await page.goto("https://www.tradingview.com/symbols/USOIL/?exchange=TVC&timeframe=5D", wait_until="networkidle")
        await asyncio.sleep(3)
        res = await page.evaluate('''() => {
            let items = [];
            const container = document.querySelector('div[data-container-name="performance-chart-id"]');
            if (!container) return 'no container';
            
            const shadowRoots = [];
            // recursive function to find elements in shadow roots just in case
            function findElements(root) {
               root.querySelectorAll('*').forEach(el => {
                   if (el.shadowRoot) {
                       findElements(el.shadowRoot);
                   }
                   if (el.tagName === 'A' || el.tagName === 'SVG' || el.className && typeof el.className === 'string' && el.className.includes('watermark') || el.className && typeof el.className === 'string' && el.className.includes('logo')) {
                       const r = el.getBoundingClientRect();
                       if (r.width > 0 && r.height > 0) {
                           items.push({
                               tag: el.tagName,
                               class: el.className,
                               href: el.href,
                               aria: el.getAttribute('aria-label'),
                               bottom: r.bottom,
                               left: r.left
                           });
                       }
                   }
               });
            }
            findElements(document.body);
            return items;
        }''')
        
        for i in res:
            try:
                # print elements near bottom left (left < 200, bottom > 200)
                if i.get('left', 0) < 200 and i.get('bottom', 0) > 300:
                    print(f"Tag: {i['tag']}, Class: {i['class']}, Href: {i['href']}, Aria: {i['aria']}")
            except:
                pass
                
        await browser.close()

asyncio.run(main())
