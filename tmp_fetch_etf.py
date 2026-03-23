import requests
import json
import re

requests.packages.urllib3.disable_warnings()

url = "https://www.ezmoney.com.tw/ETF/Fund/Info?fundCode=49YTW"
r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, verify=False)
html = r.text

if "2330" in html:
    print("Found 2330 in ezmoney HTML")
else:
    print("2330 not in ezmoney HTML, might be dynamically loaded.")

with open("tmp_ezmoney.html", "w", encoding="utf-8") as f:
    f.write(html)
