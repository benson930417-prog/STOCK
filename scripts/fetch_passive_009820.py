try:
    from scripts import fetch_passive_0050 as yuanta
except ImportError:
    import fetch_passive_0050 as yuanta


TICKER = "009820"

yuanta.HISTORY_FILE = yuanta.os.path.join(yuanta.DATA_DIR, f"passive_{TICKER}_history.json")
yuanta.LOG_FILE = yuanta.os.path.join(yuanta.DATA_DIR, f"passive_{TICKER}_log.json")
yuanta.URL = f"https://www.yuantaetfs.com/product/detail/{TICKER}/ratio"
yuanta.NAV_HISTORY_URL = f"https://www.yuantaetfs.com/tradeInfo/comparison/{TICKER}/NAVhistory#table"


if __name__ == "__main__":
    yuanta.fetch_and_update_0050()
