import os
import json
import re
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta
from io import BytesIO

# Disable SSL warnings since ezmoney certificate can sometimes fail depending on the env
requests.packages.urllib3.disable_warnings()

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
HISTORY_FILE = os.path.join(DATA_DIR, "etf_00981A_history.json")
LOG_FILE = os.path.join(DATA_DIR, "etf_00981A_log.json")


def _num(value):
    if pd.isna(value):
        return None
    text = str(value).replace(",", "").replace("%", "").strip()
    if not text or text.lower() == "nan":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _find_stock_header_row(df_raw):
    """Find the stock table, not the similarly shaped futures table above it."""
    required = {"股票代號", "股票名稱", "股數", "持股權重"}
    for i, row in df_raw.iterrows():
        labels = {str(value).strip() for value in row.values if pd.notna(value)}
        if required.issubset(labels):
            return i
    return -1


def _parse_stock_holdings(df_raw, header_row):
    headers = [
        str(value).strip() if pd.notna(value) else ""
        for value in df_raw.iloc[header_row]
    ]
    df = df_raw.iloc[header_row + 1:].copy()
    df.columns = headers
    holdings = []
    for row in df.dropna(how="all").to_dict("records"):
        sid = re.sub(r"\.0$", "", str(row.get("股票代號") or "").strip())
        name = str(row.get("股票名稱") or "").strip()
        shares = _num(row.get("股數"))
        weight = _num(row.get("持股權重"))
        if not re.fullmatch(r"\d{4}", sid) or not name or name.lower() == "nan":
            continue
        if shares is None or weight is None or shares <= 0 or weight < 0:
            continue
        holdings.append(
            {"id": sid, "name": name, "weight_pct": weight, "shares": int(shares)}
        )

    ids = [row["id"] for row in holdings]
    total_weight = sum(row["weight_pct"] for row in holdings)
    if len(holdings) < 10:
        raise RuntimeError(f"Only parsed {len(holdings)} valid stock holdings")
    if len(ids) != len(set(ids)):
        raise RuntimeError("Parsed duplicate stock ids")
    if not 20 <= total_weight <= 110:
        raise RuntimeError(f"Implausible total stock weight: {total_weight:.2f}%")
    return holdings

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
        
        # The workbook also has a futures table; require the exact stock header.
        header_row = _find_stock_header_row(df_raw)
        
        if header_row == -1:
             raise RuntimeError("Could not find the 00981A stock holding header row")
             
        file_date_str = datetime.now().strftime("%Y-%m-%d")
        fund_size = None
        outstanding_units = None
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
            elif "流通在外單位數" in row_vals:
                for val in row_list:
                    if pd.notna(val) and any(c.isdigit() for c in str(val)):
                        try:
                            v_str = str(val).replace(",", "").strip()
                            outstanding_units = int(float(v_str))
                        except Exception:
                            pass
                        
            elif "淨值" in row_vals or "NAV" in row_vals:
                for val in row_list:
                    if pd.notna(val) and ("NTD" in str(val) or any(c.isdigit() for c in str(val))):
                        try:
                            v_str = str(val).replace("NTD", "").replace(",", "").strip()
                            nav = float(v_str)
                        except: pass
        
        clean_records = _parse_stock_holdings(df_raw, header_row)
             
        closing_price = nav
        try:
            import requests as req
            rp = req.get("https://query1.finance.yahoo.com/v8/finance/chart/00981A.TW?range=14d&interval=1d", headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
            if rp.status_code == 200:
                res = rp.json()['chart']['result'][0]
                ts_list = res.get('timestamp', [])
                close_list = res.get('indicators', {}).get('quote', [{}])[0].get('close', [])
                for idx in range(len(ts_list)-1, -1, -1):
                    dt_str = datetime.fromtimestamp(ts_list[idx], timezone(timedelta(hours=8))).strftime('%Y-%m-%d')
                    if dt_str == file_date_str and close_list[idx] is not None:
                        closing_price = float(close_list[idx])
                        break
        except: pass

        day_data = {
             "date": file_date_str,
             # Upper bound on when this snapshot became public. The backtest
             # needs it to prove it never trades on data it could not have had.
             "first_seen_utc": now_utc,
             "meta": {
                 "fund_size": fund_size,
                 "outstanding_units": outstanding_units,
                 "nav": nav,
                 "closing_price": float(closing_price) if closing_price is not None else None
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
             # Keep the earliest sighting; re-running today must not make an old
             # snapshot look like it arrived later than it really did.
             if existing_day_data.get("first_seen_utc"):
                  day_data["first_seen_utc"] = existing_day_data["first_seen_utc"]

             # Deep compare the whole day_data EXCEPT closing_price which constantly floats during market open hours
             import copy
             curr_cmp = copy.deepcopy(day_data)
             prev_cmp = copy.deepcopy(existing_day_data)
             curr_cmp["meta"].pop("closing_price", None)
             prev_cmp["meta"].pop("closing_price", None)
             curr_cmp.pop("first_seen_utc", None)
             prev_cmp.pop("first_seen_utc", None)

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
        raise

def save_log(log_data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(LOG_FILE, "w", encoding="utf-8") as f:
         json.dump(log_data, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    fetch_and_update_holdings()

