import asyncio
from playwright.async_api import async_playwright

CHART_TABS = {
    "oil": "https://www.tradingview.com/symbols/USOIL/?exchange=TVC&timeframe=5D",
    "brent": "https://www.tradingview.com/symbols/RUS-BR1!/?timeframe=5D",
    "bond": "https://www.tradingview.com/symbols/TVC-US10Y/?timeframe=5D",
    "usdtwd": "https://www.tradingview.com/symbols/USDTWD/?exchange=FX_IDC&timeframe=5D",
    "usdjpy": "https://www.tradingview.com/symbols/USDJPY/?exchange=OANDA&timeframe=5D",
    "usdchf": "https://www.tradingview.com/symbols/USDCHF/?exchange=OANDA&timeframe=5D"
}

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        for key, url in CHART_TABS.items():
            print(f"Goto page... {key}")
            await page.goto(url, wait_until="networkidle", timeout=60000)
            await asyncio.sleep(2)
            
            # get symbol-header text
            result = await page.evaluate('''() => {
                const h = document.querySelector('div[class*="symbolHeader-"]') || document.querySelector('div[class*="symbol-header"]') || document.querySelector('div[class*="quotesRow-"]') || document.querySelector('div[class*="priceWrapper"]');
                if (!h) return null;
                const parts = h.innerText.split('\\n').map(p => p.trim()).filter(p => p.length > 0);
                
                let price = "0.00";
                let changePct = "0.00%";
                
                // Find percentage
                for (let i = 0; i < parts.length; i++) {
                    if (parts[i].endsWith('%')) {
                        changePct = parts[i];
                        break;
                    }
                }
                
                // For price, it's typically the first number-like string that comes after the symbol name
                for (let i = 1; i < parts.length; i++) {
                    const p = parts[i];
                    // check if it's a valid number (allowing dots and commas)
                    if (/^([0-9]+[.,0-9]*)$/.test(p)) {
                        price = p;
                        break;
                    }
                }
                
                return {price, changePct};
            }''')
            print(f"Extracted for {key}: {result}")
            
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
