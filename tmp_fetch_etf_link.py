import re

with open("tmp_ezmoney.html", "r", encoding="utf-8") as f:
    html = f.read()

# Look for getAssetXLSNPOI inside <script> tags
scripts = re.findall(r'<script.*?>(.*?)</script>', html, re.DOTALL | re.IGNORECASE)
for s in scripts:
    if "getAssetXLSNPOI" in s:
        print("Found getAssetXLSNPOI in script:")
        lines = s.split('\n')
        for i, line in enumerate(lines):
            if "getAssetXLSNPOI" in line:
                start = max(0, i - 2)
                end = min(len(lines), i + 20)
                print("\n".join(lines[start:end]))
                break
