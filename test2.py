import requests
import json
import re

def get_wcdf_price():
    url = "https://tw.stock.yahoo.com/quote/WCDF%26"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=5)
        if r.status_code == 200:
            # The page contains a large JSON blob in window.SYSTEX_QUOTE or similar.
            # But we can also just find "price":"1820.00"
            m = re.search(r'"symbolId":"WCDF&".*?"price":"([0-9\.]+)"', r.text)
            if m:
                return m.group(1)
            else:
                return "Regex failed"
        else:
            return f"Status {r.status_code}"
    except Exception as e:
        return str(e)

print(get_wcdf_price())
