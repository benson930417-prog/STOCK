import json
import os
from datetime import datetime, timezone

import requests


DATA_DIR = "data"
TICKER = "00891"
CNO = "88182265"
FID = "E0017"
HISTORY_FILE = os.path.join(DATA_DIR, f"passive_{TICKER}_history.json")
LOG_FILE = os.path.join(DATA_DIR, f"passive_{TICKER}_log.json")
URL = "https://www.ctbcinvestments.com/Etf/88182265/Combination"
API_BASE = "https://www.ctbcinvestments.com.tw/API"
TOKEN_SEED = "www.ctbcinvestments.com"


def _num(value):
    text = str(value or "").replace(",", "").replace("$", "").replace("%", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _date_key(value):
    text = str(value or "").strip()
    if not text:
        return None
    if "T" in text:
        text = text.split("T", 1)[0]
    return text.replace("/", "-")


def _load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def _post(endpoint, token, payload=None, timeout=20):
    payload = dict(payload or {})
    payload["token"] = token
    res = requests.post(
        f"{API_BASE}/{endpoint}",
        params={"token": token},
        json=payload,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json; charset=utf-8",
            "Origin": "https://www.ctbcinvestments.com.tw",
            "Referer": URL,
        },
        timeout=timeout,
    )
    res.raise_for_status()
    data = res.json()
    if data.get("ResultCode") != 0:
        raise RuntimeError(f"CTBC {endpoint} failed: {data.get('ResultMsg') or data}")
    return data.get("Data")


def _auth_token():
    data = _post("home/AuthToken", TOKEN_SEED, {})
    token = data.get("token") if isinstance(data, dict) else None
    if not token:
        raise RuntimeError("CTBC AuthToken response did not contain a token")
    return token


def _fetch_detail(token):
    data = _post("etf/ETFDetail", token, {"CNO": CNO})
    rows = data.get("FundDetail") or []
    if not rows:
        raise RuntimeError(f"CTBC ETFDetail returned no FundDetail for CNO={CNO}")
    return rows[0], data


def _fetch_holdings(token, fid):
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    data = _post("etf/ETFHoldingWeight", token, {"FID": fid, "StartDate": now_iso})
    assets = data.get("FundAssets") or []
    blocks = data.get("FundAssetsDetail") or []
    if not assets or not blocks:
        raise RuntimeError(f"CTBC ETFHoldingWeight returned no holdings for FID={fid}")
    return data


def _stock_holdings(blocks):
    stock_blocks = [block for block in blocks if block.get("Code") == "STOCK" or block.get("ColType") == 4]
    holdings = []
    for block in stock_blocks:
        for item in block.get("Data") or []:
            code = str(item.get("code_") or "").strip().upper()
            if not code:
                continue
            weight = _num(item.get("weights_"))
            if weight is None or weight <= 0:
                continue
            shares = int(_num(item.get("qty_")) or 0)
            holdings.append(
                {
                    "id": code,
                    "name": str(item.get("name_") or "").strip(),
                    "weight_pct": weight,
                    "shares": shares,
                }
            )
    if len(holdings) < 10:
        raise ValueError(f"Only parsed {len(holdings)} {TICKER} stock holdings from CTBC API")
    return holdings


def fetch_and_update_00891():
    now_utc = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    history = _load_json(HISTORY_FILE, {})
    previous_log = _load_json(LOG_FILE, {})

    token = _auth_token()
    detail, detail_data = _fetch_detail(token)
    fid = detail.get("FID") or FID
    holding_data = _fetch_holdings(token, fid)

    assets = holding_data["FundAssets"][0]
    date_key = _date_key(assets.get("資料日期") or assets.get("NAV_DT")) or now_utc[:10]
    holdings = _stock_holdings(holding_data.get("FundAssetsDetail") or [])

    fund_nav = (detail_data.get("FundNav") or [{}])[0]
    meta = {
        "fund_size": _num(assets.get("基金淨資產")),
        "nav": _num(assets.get("基金每單位淨值") or fund_nav.get("NAV")),
        "outstanding_units": int(_num(assets.get("基金在外流通單位數")) or 0) or None,
        "closing_price": None,
        "source_url": URL,
        "provider": "CTBC Investments",
        "cno": CNO,
        "fid": fid,
        "nav_history": {
            "source_url": f"{API_BASE}/etf/ETFDetail",
            "latest": {
                "date": _date_key(fund_nav.get("NAV_DT")) or date_key,
                "nav": _num(fund_nav.get("NAV")),
                "closing_price": None,
                "premium_discount": None,
                "premium_discount_pct": None,
                "fund_net_assets": _num(assets.get("基金淨資產")),
                "outstanding_units": int(_num(assets.get("基金在外流通單位數")) or 0) or None,
                "deltas": {},
            },
        },
    }

    payload = {
        "date": date_key,
        "meta": meta,
        "holdings": holdings,
    }
    previous = history.get(date_key)
    changed = previous != payload
    history[date_key] = payload
    _write_json(HISTORY_FILE, dict(sorted(history.items())))
    _write_json(
        LOG_FILE,
        {
            "last_checked_utc": now_utc,
            "last_updated_utc": now_utc if changed else previous_log.get("last_updated_utc"),
            "latest_date": date_key,
            "status": "NEW DATA FOUND" if changed else "NO CHANGE",
            "source": URL,
            "holdings_count": len(holdings),
            "provider": "CTBC Investments",
        },
    )

    if changed:
        print(f"Successfully updated {TICKER} holdings for {date_key}. Total stocks: {len(holdings)}")
    else:
        print(f"No holding changes detected for {TICKER}. Latest stored date remains {date_key}.")


if __name__ == "__main__":
    fetch_and_update_00891()
