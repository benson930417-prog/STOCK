import requests
import pandas as pd
import io

# Test fetch for yesterday's data
# ETF23 API endpoint format: /api/assetsExcel/ETF23/YYYYMMDD
res = requests.get('https://www.fhtrust.com.tw/api/assetsExcel/ETF23/20260323', headers={'User-Agent': 'Mozilla/5.0'})
if res.status_code == 200:
    print("Download successful. Parsing...")
    # Read HTML-like Excel or actual Excel depending on server
    try:
        df = pd.read_excel(io.BytesIO(res.content))
    except Exception as e:
        # Fuh Hwa often serves HTML disguised as .xls
        try:
            dfs = pd.read_html(res.content)
            df = dfs[0]
        except Exception as e2:
            print("Failed to parse as excel or HTML:", e, e2)
            exit(1)
            
    print("First 20 rows:")
    print(df.head(20).to_string())
else:
    print("Download failed, status code:", res.status_code)
