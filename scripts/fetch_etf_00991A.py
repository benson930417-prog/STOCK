import requests
import pandas as pd
import json
import os
import io
from datetime import datetime, timedelta, timezone

DATA_DIR = "data"
HISTORY_FILE = os.path.join(DATA_DIR, "etf_00991A_history.json")
LOG_FILE = os.path.join(DATA_DIR, "etf_00991A_log.json")

def fetch_and_update_00991A():
    now_utc = datetime.now(timezone.utc).isoformat()
    log_data = {"last_checked_utc": None, "last_updated_utc": None, "status": "Initializing"}
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                log_data = json.load(f)
        except Exception: pass
    log_data["last_checked_utc"] = now_utc

    history = {}
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                history = json.load(f)
        except Exception:
            pass

    # Fetch last 14 days to guarantee at least 7 valid trading days
    today = datetime.now()
    dates_to_fetch = [(today - timedelta(days=i)).strftime("%Y%m%d") for i in range(14)]
    
    updated = False
    for date_str in dates_to_fetch:
        formatted_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
        
        if formatted_date in history:
            print(f"Skipping {formatted_date} (Already cached)")
            continue
            
        print(f"Fetching {formatted_date} from Fuh Hwa...")
        try:
            res = requests.get(f'https://www.fhtrust.com.tw/api/assetsExcel/ETF23/{date_str}', headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        except Exception as e:
            print(f"  -> Request failed: {e}")
            continue
            
        if res.status_code != 200:
            print(f"  -> Failed (HTTP {res.status_code})")
            continue
            
        try:
            # Parse actual Excel format
            df = pd.read_excel(io.BytesIO(res.content))
            
            # Row 3 is Fund Size (Column 0)
            fund_size_str = str(df.iloc[3, 0]).replace(',', '')
            if fund_size_str.lower() == 'nan':
                print(f"  -> Format mismatch or empty data (likely weekend).")
                continue
                
            fund_size = float(fund_size_str)
            
            # Row 7 is NAV (Column 0)
            nav_str = str(df.iloc[7, 0]).replace(',', '')
            nav = float(nav_str)
            
            holdings = []
            for idx in range(10, len(df)):
                row = df.iloc[idx]
                vid = str(row[0]).replace('.0', '').strip()
                vname = str(row[1]).strip()
                
                if "nan" in vid.lower() or "nan" in vname.lower() or not vid:
                    continue
                if "小計" in vid or "總計" in vid or "金額" in vid or "項目" in vid or "附註" in vid:
                    continue
                    
                try:
                    shares = int(str(row[2]).replace(',', ''))
                    weight_str = str(row[4]).replace('%', '').strip()
                    weight = float(weight_str)
                    holdings.append({
                        "id": vid,
                        "name": vname,
                        "shares": shares,
                        "weight_pct": weight
                    })
                except Exception as e:
                    # Could be cash balances or other accounting lines
                    pass
                    
            closing_price = nav
            try:
                rp = requests.get("https://query1.finance.yahoo.com/v8/finance/chart/00991A.TW?range=14d&interval=1d", headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
                if rp.status_code == 200:
                    res = rp.json()['chart']['result'][0]
                    ts_list = res.get('timestamp', [])
                    close_list = res.get('indicators', {}).get('quote', [{}])[0].get('close', [])
                    for idx in range(len(ts_list)-1, -1, -1):
                        dt_str = datetime.fromtimestamp(ts_list[idx], timezone(timedelta(hours=8))).strftime('%Y-%m-%d')
                        if dt_str == formatted_date and close_list[idx] is not None:
                            closing_price = float(close_list[idx])
                            break
            except: pass

            if holdings:
                history[formatted_date] = {
                    "date": formatted_date,
                    "meta": {
                        "fund_size": fund_size,
                        "nav": nav,
                        "closing_price": float(closing_price)
                    },
                    "holdings": holdings
                }
                updated = True
                print(f"  -> Success. Extracted {len(holdings)} holding records.")
            else:
                print(f"  -> No usable holding records found.")
        except Exception as e:
            print(f"  -> Parse Error: {e}")
            
    if updated:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        log_data["last_updated_utc"] = now_utc
        log_data["status"] = "NEW DATA FOUND"
    else:
        log_data["status"] = "No Change"
        
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(log_data, f, ensure_ascii=False, indent=2)
            
if __name__ == "__main__":
    fetch_and_update_00991A()
