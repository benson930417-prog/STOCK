import requests
import re
import json

def test():
    r = requests.get('https://tw.stock.yahoo.com/quote/WCDF%26', headers={'User-Agent': 'Mozilla/5.0'})
    print("Status:", r.status_code)
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.+?)</script>', r.text)
    if m:
        data = json.loads(m.group(1))
        # Navigate JSON structure carefully
        try:
            quote = data['props']['pageProps']['initialState']['systexQuote']['systexQuoteItem']['WCDF&']
            print("Price:", quote.get('price'))
            print("Change:", quote.get('change'))
            print("Change %:", quote.get('changePercent'))
        except KeyError as e:
            print("KeyError:", e)
    else:
        print("Not found")

test()
