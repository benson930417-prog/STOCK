import requests
import re

def test():
    r = requests.get('https://tw.stock.yahoo.com/quote/WCDF%26', headers={'User-Agent': 'Mozilla/5.0'})
    m = re.search(r'"price":"([0-9\.,]+)","change":"([0-9\.\-+]+)","changePercent":"([0-9\.\-+%]+)"', r.text)
    if m:
        print("Success:", m.groups())
    else:
        print("Failed to find regex")
test()
