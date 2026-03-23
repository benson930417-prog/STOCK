import re

with open("tmp_ezmoney.html", "r", encoding="utf-8") as f:
    html = f.read()

rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL | re.IGNORECASE)
for r in rows:
    if "2330" in r:
        cells = re.findall(r'<td[^>]*>(.*?)</td>', r, re.DOTALL | re.IGNORECASE)
        # strip inner span or div
        clean_cells = [re.sub(r'<[^>]*>', '', c).strip() for c in cells]
        print("Columns for TSMC:", clean_cells)
        break
