import json
import os
from playwright.sync_api import sync_playwright

SUMMARY_DIR = os.path.join("data", "summaries")
ETFS = [
    ("00981A", "主動統一台股增長 (00981A)"),
    ("00988A", "主動統一全球創新 (00988A)"),
    ("00997A", "主動群益美國增長 (00997A)"),
]

def load_data(json_path):
    if not os.path.exists(json_path): return None, None, None, None
    with open(json_path, 'r', encoding='utf-8') as f: history = json.load(f)
    if not history: return None, None, None, None
    dates = sorted(history.keys(), reverse=True)
    if len(dates) < 2: return dates[0], history[dates[0]], None, None
    return dates[0], history[dates[0]], dates[1], history[dates[1]]

def fmt_money(am):
    if abs(am) < 1000000.0: return "<100 萬"
    sign = "+" if am > 0 else "-"
    am = abs(am)
    if am >= 100000000: return f"{sign}{am/100000000:.2f} 億"
    elif am >= 10000: return f"{sign}{am/10000:.0f} 萬"
    else: return f"{sign}{am:,.0f}"

def share_change_text(ticker, share_diff):
    sign = "+" if share_diff > 0 else ""
    if ticker in {"00988A", "00997A"}:
        return f"{sign}{share_diff:,.0f} 股"
    return f"{sign}{share_diff // 1000:,} 張"

def render_html(title, data_curr, date_curr, data_prev, date_prev):
    if not data_curr or not data_prev: return False
    ticker = title.split("(")[-1].split(")")[0] if "(" in title and ")" in title else ""
    
    meta_c = data_curr.get('meta', {})
    meta_p = data_prev.get('meta', {})
    fs_c = meta_c.get('fund_size', 0)
    fs_p = meta_p.get('fund_size', 0)
    nav_c = meta_c.get('nav', 0)
    price_c = meta_c.get('closing_price', 0)
    
    fs_diff_pct = ((fs_c - fs_p)/fs_p*100) if fs_p else 0.0
    prem_pct = ((price_c - nav_c)/nav_c*100) if nav_c else 0.0
    fs_str = f"{fs_c/100000000:.0f}&nbsp;億" # Use &nbsp; to prevent wrapping
    
    if abs(fs_diff_pct) < 0.005:
        fs_diff_str = "0.00%"
        fs_color_class = "text-gray-900"
    else:
        fs_diff_str = f"{fs_diff_pct:+.2f}%"
        fs_color_class = "text-[#CC2400]" if fs_diff_pct > 0 else "text-[#258C18]"
        
    if abs(prem_pct) < 0.005:
        prem_str = "0.00%"
        prem_color_class = "text-gray-700 bg-gray-100"
    else:
        prem_str = f"{prem_pct:+.2f}%"
        prem_color_class = "text-[#CC2400] bg-red-50" if prem_pct > 0 else "text-[#258C18] bg-green-50"
        
    ph_map = {h['id']: h for h in data_prev.get('holdings', [])}
    ch_map = {h['id']: h for h in data_curr.get('holdings', [])}
    
    new_s, del_s, inc_s, dec_s = [], [], [], []
    for sid, ch in ch_map.items():
        if sid not in ph_map: new_s.append(ch)
        else:
            ds = ch['shares'] - ph_map[sid]['shares']
            if ds > 0: inc_s.append((ch, ph_map[sid]))
            elif ds < 0: dec_s.append((ch, ph_map[sid]))
    for sid, ph in ph_map.items():
        if sid not in ch_map: del_s.append(ph)

    rows = []
    for h in new_s: rows.append({"name": h['name'], "st": "new", "sd": h['shares'], "cw": h['weight_pct'], "pw": 0, "aw": h['weight_pct']})
    for h in del_s: rows.append({"name": h['name'], "st": "del", "sd": -h['shares'], "cw": 0, "pw": h['weight_pct'], "aw": -h['weight_pct']})
    for ch, ph in inc_s: rows.append({"name": ch['name'], "st": "inc", "sd": ch['shares']-ph['shares'], "cw": ch['weight_pct'], "pw": ph['weight_pct'], "aw": (ch['shares']-ph['shares'])*(ch['weight_pct']/ch['shares']) if ch['shares'] else 0})
    for ch, ph in dec_s: rows.append({"name": ch['name'], "st": "dec", "sd": ch['shares']-ph['shares'], "cw": ch['weight_pct'], "pw": ph['weight_pct'], "aw": (ch['shares']-ph['shares'])*(ph['weight_pct']/ph['shares']) if ph['shares'] else 0})

    for r in rows: r['am'] = (r['aw'] / 100.0) * fs_c
    rows.sort(key=lambda x: x['am'], reverse=True)
    
    max_am = max([abs(r['am']) for r in rows]) if rows else 1
    
    name_part = title.split(' (')[0]
    ticker_part = f"({title.split(' (')[1]}" if ' (' in title else ""
    
    html = f"""
    <!DOCTYPE html>
    <html lang="zh-TW">
    <head>
        <meta charset="UTF-8">
        <script src="https://cdn.tailwindcss.com"></script>
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800;900&family=Noto+Sans+TC:wght@400;700;900&display=swap');
            body {{ font-family: 'Inter', 'Noto Sans TC', sans-serif; background-color: white; }}
        </style>
    </head>
    <body class="p-10 w-[960px] bg-white text-gray-900 border border-gray-100 rounded-lg shadow-md">
        
        <!-- Header -->
        <div class="flex flex-row justify-between items-end border-b border-gray-200 pb-5 mb-8">
            <!-- Left: Titles -->
            <div class="flex-1 pr-6">
                <h1 class="text-[32px] font-black text-[#111111] tracking-tight leading-tight mb-2 whitespace-nowrap">
                    {name_part}<br>
                    {ticker_part}
                </h1>
                <div class="flex items-center space-x-3 mt-1">
                    <p class="text-lg font-bold text-gray-500 tracking-wide">{date_curr} 操作日報</p>
                    <span class="px-3 py-1 border border-gray-100 text-[13px] tracking-wide font-bold rounded-full {prem_color_class}">折溢價 {prem_str}</span>
                </div>
            </div>
            
            <!-- Right: Metrics -->
            <div class="flex items-end shrink-0">
                <!-- Metric 1: Fund Size -->
                <div class="text-right">
                    <p class="text-lg font-bold text-gray-500 tracking-wide mb-1">基金規模 (TWD)</p>
                    <div class="flex items-baseline justify-end space-x-2">
                        <span class="text-[44px] leading-none font-black text-[#111111] whitespace-nowrap">{fs_str}</span>
                        <span class="text-[20px] font-bold {fs_color_class} whitespace-nowrap">{fs_diff_str}</span>
                    </div>
                </div>
            </div>
        </div>

        <!-- 4 KPI Boxes -->
        <div class="grid grid-cols-4 gap-4 mb-10">
            <!-- Inc -->
            <div class="bg-[#FFEAEA] rounded-md p-5 border-l-[6px] border-[#EB2F06] relative">
                <p class="text-[#EB2F06] font-bold text-[17px] mb-1">加碼</p>
                <p class="text-4xl font-black text-[#B01700]">{len(inc_s)} <span class="text-lg font-medium text-[#EB2F06]/70">檔</span></p>
            </div>
            <!-- Dec -->
            <div class="bg-[#EAFCDE] rounded-md p-5 border-l-[6px] border-[#44BD32] relative">
                <p class="text-[#44BD32] font-bold text-[17px] mb-1">減碼</p>
                <p class="text-4xl font-black text-[#1E7710]">{len(dec_s)} <span class="text-lg font-medium text-[#44BD32]/70">檔</span></p>
            </div>
            <!-- New -->
            <div class="bg-[#FFE6F2] rounded-md p-5 border-l-[6px] border-[#E84393] relative">
                <p class="text-[#E84393] font-bold text-[17px] mb-1">新增</p>
                <p class="text-4xl font-black text-[#A0165B]">{len(new_s)} <span class="text-lg font-medium text-[#E84393]/70">檔</span></p>
            </div>
            <!-- Del -->
            <div class="bg-[#F0F0F0] rounded-md p-5 border-l-[6px] border-[#718093] relative">
                <p class="text-[#718093] font-bold text-[17px] mb-1">刪除</p>
                <p class="text-4xl font-black text-[#2F3640]">{len(del_s)} <span class="text-lg font-medium text-[#718093]/70">檔</span></p>
            </div>
        </div>

        <h2 class="text-[26px] font-bold mb-6 text-gray-800 border-b border-gray-100 pb-3 tracking-tight">資金分配變動 <span class="text-[16px] font-normal text-gray-400 ml-2 tracking-wide">(估值, 億 TWD)</span></h2>
        
        <div class="space-y-6">
    """
    
    if not rows:
        html += '<p class="text-center text-gray-400 font-medium py-10">無資金變動 / No Changes</p>'
    else:
        for r in rows:
            w_pct = abs(r['am']) / max_am * 100
            w_pct = max(w_pct, 1) # min 1% width
            color_class = { 'new': 'bg-[#E84393]', 'del': 'bg-[#718093]', 'inc': 'bg-[#EB2F06]', 'dec': 'bg-[#44BD32]' }[r['st']]
            sign = "+" if r['sd'] > 0 else ""
            
            html += f"""
            <div class="flex items-center">
                <!-- Label -->
                <div class="w-36 text-right pr-4 text-[18px] font-bold text-gray-800 whitespace-nowrap truncate">{r['name']}</div>
                <!-- Track -->
                <div class="flex-1 flex items-center relative h-11 rounded bg-gray-50 overflow-visible">
                    <!-- 0 Line marker -->
                    <div class="absolute left-1/2 top-[-4px] bottom-[-4px] w-[2px] bg-gray-300 z-0"></div>
            """
            
            # Left or right bar
            if r['am'] < 0:
                html += f"""
                    <div class="w-1/2 flex justify-end z-10">
                        <div class="h-9 {color_class} shadow-sm rounded-l-sm" style="width: {w_pct}%"></div>
                    </div>
                    <div class="w-1/2 z-10 pl-3 flex items-center">
                        <span class="font-black text-[19px] text-[#258C18] whitespace-nowrap tracking-wide">{fmt_money(r['am'])}</span>
                        <span class="text-gray-500 text-[15px] font-medium ml-3 whitespace-nowrap">({share_change_text(ticker, r['sd'])})</span>
                        <span class="text-gray-400 text-[15px] ml-2 hidden sm:inline whitespace-nowrap">({r['pw']:.2f}% &rarr; {r['cw']:.2f}%)</span>
                    </div>
                """
            else:
                html += f"""
                    <div class="w-1/2 z-10 pr-3 flex items-center justify-end">
                        <span class="text-gray-400 text-[15px] mr-2 hidden sm:inline whitespace-nowrap">({r['pw']:.2f}% &rarr; {r['cw']:.2f}%)</span>
                        <span class="text-gray-500 text-[15px] font-medium mr-3 whitespace-nowrap">({share_change_text(ticker, r['sd'])})</span>
                        <span class="font-black text-[19px] text-[#CC2400] whitespace-nowrap tracking-wide">{fmt_money(r['am'])}</span>
                    </div>
                    <div class="w-1/2 flex justify-start z-10">
                        <div class="h-9 {color_class} shadow-sm rounded-r-sm" style="width: {w_pct}%"></div>
                    </div>
                """
                
            html += """
                </div>
            </div>
            """

    html += """
        </div>
    </body>
    </html>
    """
    return html

def generate(selected_tickers=None):
    os.makedirs(SUMMARY_DIR, exist_ok=True)
    selected_tickers = set(selected_tickers or [])

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(device_scale_factor=2)

        for ticker, title in ETFS:
            if selected_tickers and ticker not in selected_tickers:
                continue
            date_curr, data_curr, date_prev, data_prev = load_data(f"data/etf_{ticker}_history.json")
            html = render_html(title, data_curr, date_curr, data_prev or data_curr, date_prev or date_curr)
            if html:
                page.set_content(html)
                # wait for network idle to ensure fonts load
                page.wait_for_load_state("networkidle")
                element = page.locator("body")
                element.screenshot(
                    path=os.path.join(SUMMARY_DIR, f"etf_{ticker}_summary_latest.jpg"),
                    type="jpeg",
                    quality=95,
                )
                print(f"Saved {ticker} latest image")

        browser.close()

if __name__ == '__main__':
    generate()
