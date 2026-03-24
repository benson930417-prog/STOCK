import os
import json
import requests
import pandas as pd
from datetime import datetime, timezone
from io import BytesIO

# Disable SSL warnings since ezmoney certificate can sometimes fail depending on the env
requests.packages.urllib3.disable_warnings()

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
HISTORY_FILE = os.path.join(DATA_DIR, "etf_00981A_history.json")
LOG_FILE = os.path.join(DATA_DIR, "etf_00981A_log.json")

def fetch_and_update_holdings():
    now_utc = datetime.now(timezone.utc).isoformat()
    print(f"[{now_utc}] Fetching ETF holdings...")
    url = "https://www.ezmoney.com.tw/ETF/Fund/AssetExcelNPOI?fundCode=49YTW"
    
    # Load existing logs
    log_data = {"last_checked_utc": None, "last_updated_utc": None, "status": "Initializing"}
    if os.path.exists(LOG_FILE):
         try:
              with open(LOG_FILE, "r", encoding="utf-8") as f:
                   log_data = json.load(f)
         except Exception:
              pass
              
    log_data["last_checked_utc"] = now_utc
    
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, verify=False, timeout=30)
        r.raise_for_status()
        
        # Read Excel from memory
        excel_data = BytesIO(r.content)
        df_raw = pd.read_excel(excel_data, header=None)
        
        # Find where the actual table headers are
        header_row = -1
        for i, row in df_raw.iterrows():
            row_str = " ".join([str(x) for x in row.values])
            if "代碼" in row_str or "名稱" in row_str:
                header_row = i
                break
        
        if header_row == -1:
             print("Could not find table headers in the excel file.")
             log_data["status"] = "Error parsing Excel"
             save_log(log_data)
             return
             
        headers = df_raw.iloc[header_row].fillna("").astype(str).tolist()
        df = df_raw.iloc[header_row+1:].copy()
        df.columns = headers
        df = df.dropna(how='all')
        records = df.to_dict('records')
        
        file_date_str = datetime.now().strftime("%Y-%m-%d")
        fund_size = None
        nav = None
        
        for i in range(header_row):
            row_list = df_raw.iloc[i].values
            row_strs = [str(x) for x in row_list if pd.notna(x)]
            row_vals = " ".join(row_strs)
            
            if "日期" in row_vals or "Date" in row_vals:
                import re
                m = re.search(r'(\d{3,4})[-/](\d{1,2})[-/](\d{1,2})', row_vals)
                if m:
                    year = int(m.group(1))
                    if year < 1000:
                        year += 1911
                    file_date_str = f"{year}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
                    
            elif "淨資產" in row_vals or "規模" in row_vals:
                for val in row_list:
                    if pd.notna(val) and ("NTD" in str(val) or any(c.isdigit() for c in str(val))):
                        try:
                            v_str = str(val).replace("NTD", "").replace(",", "").strip()
                            fund_size = float(v_str)
                        except: pass
                        
            elif "淨值" in row_vals or "NAV" in row_vals:
                for val in row_list:
                    if pd.notna(val) and ("NTD" in str(val) or any(c.isdigit() for c in str(val))):
                        try:
                            v_str = str(val).replace("NTD", "").replace(",", "").strip()
                            nav = float(v_str)
                        except: pass
        
        clean_records = []
        for r in records:
             sid, sname, weight, shares = None, None, None, None
             for k, v in r.items():
                  k_str = str(k)
                  if "代" in k_str or "股票代碼" in k_str:
                       sid = str(v).strip()
                  elif "名" in k_str or "股票名稱" in k_str:
                       sname = str(v).strip()
                  elif "權重" in k_str or "比例" in k_str or "%" in k_str:
                       try: weight = float(str(v).replace("%", ""))
                       except Exception: pass
                  elif "股數" in k_str or "持股" in k_str:
                       try: shares = int(str(v).replace(",", ""))
                       except Exception: pass
                            
             if sid and str(sid) != "nan" and sname and str(sname) != "nan":
                  clean_records.append({
                       "id": sid,
                       "name": sname,
                       "weight_pct": weight,
                       "shares": shares
                  })
                  
        if not clean_records:
             print("Parsed no valid stock records.")
             log_data["status"] = "No valid records found"
             save_log(log_data)
             return
             
        closing_price = nav
        try:
            import requests as req
            rp = req.get("https://query1.finance.yahoo.com/v8/finance/chart/00981A.TW", headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
            if rp.status_code == 200:
                closing_price = rp.json()['chart']['result'][0]['meta']['regularMarketPrice']
        except: pass

        day_data = {
             "date": file_date_str,
             "meta": {
                 "fund_size": fund_size,
                 "nav": nav,
                 "closing_price": float(closing_price)
             },
             "holdings": clean_records
        }
        
        history = {}
        if os.path.exists(HISTORY_FILE):
             try:
                  with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                       history = json.load(f)
             except Exception:
                  pass
                  
        # Detect Changes
        is_changed = True
        existing_day_data = history.get(file_date_str)
        
        if existing_day_data:
             # Deep compare the whole day_data EXCEPT closing_price which constantly floats during market open hours
             import copy
             curr_cmp = copy.deepcopy(day_data)
             prev_cmp = copy.deepcopy(existing_day_data)
             curr_cmp["meta"].pop("closing_price", None)
             prev_cmp["meta"].pop("closing_price", None)
             
             if json.dumps(curr_cmp, sort_keys=True) == json.dumps(prev_cmp, sort_keys=True):
                  is_changed = False
                  
        history[file_date_str] = day_data
        
        os.makedirs(DATA_DIR, exist_ok=True)
        
        if is_changed:
             log_data["last_updated_utc"] = now_utc
             log_data["status"] = "NEW DATA FOUND"
             with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                  json.dump(history, f, ensure_ascii=False, indent=2)
             print(f"Successfully updated ETF holdings for {file_date_str}. Total stocks: {len(clean_records)}")
        else:
             log_data["status"] = "No Change"
             print(f"No changes detected for {file_date_str}.")
             
        save_log(log_data)
        
    except Exception as e:
        print(f"Error fetching/parsing ETF data: {e}")
        log_data["status"] = f"Error: {e}"
        save_log(log_data)

def save_log(log_data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(LOG_FILE, "w", encoding="utf-8") as f:
         json.dump(log_data, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    fetch_and_update_holdings()

