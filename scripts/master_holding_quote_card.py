import json
import sys
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.monitor_etf_quotes import (
    TSMC_PROXY_TARGETS,
    _apply_tsmc_night_futures_proxy,
    _is_live_market_session,
    _select_tsmc_proxy,
    _tsmc_data_mode,
    _tsmc_proxy_cache_status,
    fetch_yahoo_quotes,
)
from scripts.generate_quote_card import generate_quote_card_from_cache
from scripts.master_manual_positions import (
    CASH_LABEL,
    cash_row,
    load_manual_positions,
    manual_positions_as_open_position_rows,
)

DATA_DIR = ROOT_DIR / "data"
IMAGE_DIR = DATA_DIR / "images"
QUOTE_CACHE_DIR = DATA_DIR / "quote_cache"
MASTER_PATH = DATA_DIR / "master_trades.csv"
MASTER_CACHE_PATH = QUOTE_CACHE_DIR / "master_holding.json"

SELL_FEE_RATE = 0.001425 * 0.28
SELL_STOCK_TAX_RATE = 0.003
SELL_ETF_TAX_RATE = 0.001
ETF_NAME_TO_TICKER = {
    "主動統一台股增長": "00981A",
    "主動群益美國增長": "00997A",
    "元大台灣50": "0050",
    "國泰費城半導體": "00830",
    "國泰永續高股息": "00878",
    "新光美國電力基建": "009805",
    "元大納斯達克精選": "009820",
    "期元大S&P黃金": "00635U",
    "國泰US短期公債": "00865B",
}
ETF_TICKER_TO_NAME = {v: k for k, v in ETF_NAME_TO_TICKER.items()}


def _default_sell_tax_rate_for_position(item):
    code = str(item.get("code") or item.get("ticker") or "").upper()
    ticker = str(item.get("ticker") or "").upper()
    if ticker or code.startswith("00"):
        return SELL_ETF_TAX_RATE
    return SELL_STOCK_TAX_RATE


def _sell_tax_rate_for_position(item):
    bank_rate = item.get("bank_sell_tax_rate")
    if bank_rate is not None and not pd.isna(bank_rate):
        return float(bank_rate)
    return _default_sell_tax_rate_for_position(item)


def _infer_bank_sell_tax_rates(raw_trades):
    by_stock = defaultdict(list)
    by_class = defaultdict(list)
    required = {"股名", "成交股數", "成交價", "交易稅"}
    if raw_trades.empty or not required.issubset(raw_trades.columns):
        return {}, {}

    for _, row in raw_trades.iterrows():
        qty = _to_int(row.get("成交股數"))
        price = _to_float(row.get("成交價"))
        tax = _to_float(row.get("交易稅"))
        gross = qty * price
        if qty <= 0 or price <= 0 or tax <= 0 or gross <= 0:
            continue

        rate = tax / gross
        if not 0 < rate < 0.01:
            continue

        stock = str(row.get("股名", "")).strip()
        if stock:
            by_stock[stock].append(rate)
        product_class = "etf" if ETF_NAME_TO_TICKER.get(stock) or rate < 0.002 else "stock"
        by_class[product_class].append(rate)

    return (
        {stock: float(pd.Series(rates).median()) for stock, rates in by_stock.items() if rates},
        {product_class: float(pd.Series(rates).median()) for product_class, rates in by_class.items() if rates},
    )


def _bank_tax_rate_for_open_position(stock, ticker, stock_rates, class_rates):
    if stock in stock_rates:
        return stock_rates[stock]
    product_class = "etf" if ticker else "stock"
    if product_class in class_rates:
        return class_rates[product_class]
    return _default_sell_tax_rate_for_position({"stock": stock, "ticker": ticker})


def _to_float(value):
    if pd.isna(value):
        return 0.0
    return float(str(value).replace(",", "").strip() or 0)


def _to_int(value):
    if pd.isna(value):
        return 0
    return int(float(str(value).replace(",", "").strip() or 0))


def _fmt_money(value):
    if value is None:
        return "----"
    return f"{value:,.0f}"


def load_master_trades():
    return pd.read_csv(MASTER_PATH, encoding="utf-8-sig")


def calculate_open_positions(raw_trades):
    df = raw_trades.copy()
    if df.empty:
        return pd.DataFrame()

    df["日期"] = pd.to_datetime(df["日期"])
    df["成交股數"] = df["成交股數"].apply(_to_int)
    df["淨收付金額"] = df["淨收付金額"].apply(_to_float)
    df["股名"] = df["股名"].astype(str).str.strip()
    if "買賣別" in df.columns:
        df = df[~df["買賣別"].isin(["沖買", "沖賣"])]
    df = df.sort_values(["股名", "日期"]).reset_index(drop=True)
    bank_stock_tax_rates, bank_class_tax_rates = _infer_bank_sell_tax_rates(raw_trades)

    inventory = defaultdict(deque)
    for stock, sdf in df.groupby("股名", sort=False):
        for _, row in sdf.sort_values("日期").iterrows():
            qty = int(row["成交股數"])
            cash = float(row["淨收付金額"])
            if qty <= 0:
                continue
            if cash < 0:
                inventory[stock].append({"qty": qty, "cost": -cash})
            elif cash > 0:
                remaining = qty
                while remaining > 0 and inventory[stock]:
                    lot = inventory[stock][0]
                    take = min(remaining, lot["qty"])
                    ratio = take / lot["qty"] if lot["qty"] else 0
                    lot["qty"] -= take
                    lot["cost"] -= lot["cost"] * ratio
                    remaining -= take
                    if lot["qty"] <= 0:
                        inventory[stock].popleft()

    rows = []
    for stock, lots in inventory.items():
        qty = sum(int(lot["qty"]) for lot in lots)
        if qty <= 0:
            continue
        total_cost = sum(float(lot["cost"]) for lot in lots)
        ticker = ETF_NAME_TO_TICKER.get(stock)
        rows.append({
            "stock": stock,
            "shares": qty,
            "cost": total_cost,
            "avg_cost": total_cost / qty if qty else 0.0,
            "ticker": ticker,
            "bank_sell_tax_rate": _bank_tax_rate_for_open_position(
                stock,
                ticker,
                bank_stock_tax_rates,
                bank_class_tax_rates,
            ),
        })
    return pd.DataFrame(rows).sort_values("cost", ascending=False).reset_index(drop=True) if rows else pd.DataFrame()


def _stock_name_symbol_map():
    options = {}
    try:
        r1 = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", verify=False, timeout=5)
        if r1.status_code == 200:
            for item in r1.json():
                code = str(item.get("Code", "")).strip()
                name = str(item.get("Name", "")).strip()
                if code and name:
                    options[name] = {"code": code, "symbol": f"{code}.TW"}
    except Exception:
        pass
    try:
        r2 = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes", verify=False, timeout=5)
        if r2.status_code == 200:
            for item in r2.json():
                code = str(item.get("SecuritiesCompanyCode", "")).strip()
                name = str(item.get("CompanyName", "")).strip()
                if code and name:
                    options[name] = {"code": code, "symbol": f"{code}.TWO"}
    except Exception:
        pass
    return options


def enrich_positions_with_quotes(positions):
    if positions.empty:
        return positions

    name_map = _stock_name_symbol_map()
    rows = []
    symbols = []
    for _, row in positions.iterrows():
        item = row.to_dict()
        ticker = item.get("ticker")
        if ticker:
            symbol, country, code = f"{ticker}.TW", "TW", ticker
        else:
            info = name_map.get(str(item.get("stock", "")).strip())
            symbol = info["symbol"] if info else None
            country = "TW" if info else None
            code = info["code"] if info else item.get("stock")
        item.update({"symbol": symbol, "country": country, "code": code})
        rows.append(item)
        if symbol:
            symbols.append((symbol, country))

    quotes = fetch_yahoo_quotes(symbols, max_workers=10)
    has_tsmc_proxy_target = any(str(symbol or "").upper() in TSMC_PROXY_TARGETS for symbol, _ in symbols)
    tsmc_data_mode = _tsmc_data_mode()
    tsmc_proxy = None
    if has_tsmc_proxy_target:
        tsmc_proxy = _select_tsmc_proxy(tsmc_data_mode)
    for item in rows:
        quote = quotes.get(item.get("symbol")) or {}
        quote = _apply_tsmc_night_futures_proxy(quote, item.get("symbol"), tsmc_proxy)
        price = quote.get("regularMarketPrice")
        day_pct = quote.get("regularMarketChangePercent")
        if price is None and item.get("ticker"):
            price = _latest_etf_close(item["ticker"])
        item["price"] = float(price) if price is not None else None
        item["day_change_pct"] = float(day_pct) if day_pct is not None else None
        quote_time = quote.get("regularMarketTime")
        item["quote_time_utc"] = (
            datetime.fromtimestamp(int(quote_time), timezone.utc).isoformat().replace("+00:00", "Z")
            if quote_time else quote.get("regularMarketTimeUtc")
        )
        item["market_session"] = quote.get("marketSession") or quote.get("market_session")
        item["proxy"] = quote.get("proxy")
        item["market_value"] = item["shares"] * item["price"] if item["price"] is not None else None
        item["est_sell_fee"] = item["market_value"] * SELL_FEE_RATE if item["market_value"] is not None else None
        item["sell_tax_rate"] = _sell_tax_rate_for_position(item)
        item["est_sell_tax"] = item["market_value"] * item["sell_tax_rate"] if item["market_value"] is not None else None
        item["liquidation_value"] = (
            item["market_value"] - item["est_sell_fee"] - item["est_sell_tax"]
            if item["market_value"] is not None else None
        )
        item["unrealized_pnl"] = item["liquidation_value"] - item["cost"] if item["liquidation_value"] is not None else None

    out = pd.DataFrame(rows)
    if "market_value" in out and out["market_value"].dropna().sum():
        out["weight_pct"] = out["market_value"] / out["market_value"].sum() * 100.0
    out.attrs["tsmc_proxy"] = _tsmc_proxy_cache_status(has_tsmc_proxy_target, tsmc_proxy)
    out.attrs["tsmc_data_mode"] = tsmc_data_mode
    return out


def _latest_history_payload(ticker):
    path = DATA_DIR / (f"passive_{ticker}_history.json" if ticker in {"0050", "00830", "00878", "009805", "009820"} else f"etf_{ticker}_history.json")
    if not path.exists():
        return None, {}
    history = json.loads(path.read_text(encoding="utf-8"))
    date_key = max(history.keys())
    return date_key, history[date_key]


def _latest_etf_close(ticker):
    _, payload = _latest_history_payload(ticker)
    meta = payload.get("meta", {})
    price = meta.get("closing_price") or meta.get("price")
    try:
        return float(price) if price is not None else None
    except (TypeError, ValueError):
        return None


def _quote_cache_by_holding(ticker):
    path = QUOTE_CACHE_DIR / f"etf_{ticker}_quotes.json"
    if not path.exists():
        return {}
    cache = json.loads(path.read_text(encoding="utf-8"))
    return {str(row.get("id")): row for row in cache.get("holdings", [])}


def _normalize_underlying_key(holding_id, country=None):
    raw = str(holding_id or "").strip().upper()
    parts = raw.split()
    symbol = parts[0] if parts else raw
    market = parts[1] if len(parts) > 1 else ""
    for suffix in [".US", ".TW", ".JP", ".HK", ".T"]:
        if symbol.endswith(suffix):
            symbol = symbol[:-len(suffix)]
            market = market or suffix[1:]
            break
    inferred_country = country or None
    if not inferred_country:
        if market in {"US", "JP", "HK", "TW", "T", "TWO"}:
            if market == "T":
                inferred_country = "JP"
            else:
                inferred_country = "TW" if market == "TWO" else market
        elif raw.endswith(".TW") or raw.endswith(".TWO"):
            inferred_country = "TW"
        elif raw.endswith(".T"):
            inferred_country = "JP"
        elif raw.endswith(".HK"):
            inferred_country = "HK"
        elif raw.endswith(".US"):
            inferred_country = "US"
        elif symbol.isdigit():
            inferred_country = "TW"
        else:
            inferred_country = "US"
    return f"{inferred_country}:{symbol}", inferred_country, symbol


def build_expanded_exposure(position_quotes):
    exposures = {}
    for _, pos in position_quotes.dropna(subset=["market_value"]).iterrows():
        if pos.get("stock") == CASH_LABEL or pos.get("code") == CASH_LABEL:
            continue
        ticker = pos.get("ticker")
        if ticker not in {"00981A", "00997A", "0050", "00830", "00878", "009805", "009820"}:
            key, country, code = _normalize_underlying_key(pos.get("code"), pos.get("country"))
            exposures[key] = {
                "key": key,
                "code": code,
                "name": pos.get("stock"),
                "country": country,
                "value_twd": exposures.get(key, {}).get("value_twd", 0.0) + float(pos["market_value"]),
                "source_parts": ["直接持股"],
                "day_change_pct": pos.get("day_change_pct"),
                "quote_time_utc": pos.get("quote_time_utc"),
                "market_session": pos.get("market_session"),
                "proxy": pos.get("proxy"),
            }
            continue

        _, payload = _latest_history_payload(ticker)
        quote_map = _quote_cache_by_holding(ticker)
        for holding in payload.get("holdings", []):
            weight = holding.get("weight_pct")
            if weight is None:
                continue
            quote_row = quote_map.get(str(holding.get("id")), {})
            key, country, code = _normalize_underlying_key(holding.get("id"), quote_row.get("country"))
            value = float(pos["market_value"]) * float(weight) / 100.0
            if key not in exposures:
                exposures[key] = {
                    "key": key,
                    "code": code,
                    "name": holding.get("name"),
                    "country": country,
                    "value_twd": 0.0,
                    "source_parts": [],
                    "weighted_move_sum": 0.0,
                    "move_weight": 0.0,
                    "quote_time_utc": quote_row.get("quote_time_utc"),
                    "market_session": quote_row.get("market_session"),
                    "proxy": quote_row.get("proxy"),
                }
            exposures[key]["value_twd"] += value
            exposures[key]["source_parts"].append(f"{ticker} {float(weight):.2f}%")
            if quote_row.get("day_change_pct") is not None:
                exposures[key]["weighted_move_sum"] += value * float(quote_row["day_change_pct"])
                exposures[key]["move_weight"] += value
            if quote_row.get("quote_time_utc"):
                exposures[key]["quote_time_utc"] = quote_row.get("quote_time_utc")
            if quote_row.get("market_session"):
                exposures[key]["market_session"] = quote_row.get("market_session")
            if quote_row.get("proxy"):
                exposures[key]["proxy"] = quote_row.get("proxy")

    total = sum(item.get("value_twd", 0.0) for item in exposures.values())
    rows = []
    for item in exposures.values():
        day_change = item.get("day_change_pct")
        if day_change is None and item.get("move_weight"):
            day_change = item["weighted_move_sum"] / item["move_weight"]
        rows.append({
            "name": item.get("name") or item.get("code"),
            "code": item.get("code"),
            "country": item.get("country") or "--",
            "source": " / ".join(sorted(set(item.get("source_parts", [])))),
            "value_twd": item.get("value_twd", 0.0),
            "weight_pct": item.get("value_twd", 0.0) / total * 100.0 if total else 0.0,
            "day_change_pct": day_change,
            "quote_time_utc": item.get("quote_time_utc"),
            "market_session": item.get("market_session"),
            "proxy": item.get("proxy"),
        })
    return sorted(rows, key=lambda row: row["weight_pct"], reverse=True)


def load_master_snapshot():
    manual = load_manual_positions()
    base = calculate_open_positions(load_master_trades())
    existing_tickers = (
        set(base["ticker"].dropna().astype(str).str.upper().tolist())
        if not base.empty and "ticker" in base else set()
    )
    extra_rows = manual_positions_as_open_position_rows(manual, existing_tickers=existing_tickers)
    if extra_rows:
        base = pd.concat([base, pd.DataFrame(extra_rows)], ignore_index=True) if not base.empty else pd.DataFrame(extra_rows)
    positions = enrich_positions_with_quotes(base) if not base.empty else base

    # Append cash AFTER enrichment so it bypasses Yahoo lookups.
    cash = cash_row(manual.get("cash_twd"))
    cash_amount = float(cash.get("market_value", 0.0)) if cash is not None else 0.0
    if cash is not None:
        if positions.empty:
            positions = pd.DataFrame([cash])
        else:
            positions = pd.concat([positions, pd.DataFrame([cash])], ignore_index=True)
        # Re-normalise weights with the cash row included.
        if "market_value" in positions and positions["market_value"].dropna().sum():
            positions["weight_pct"] = positions["market_value"] / positions["market_value"].sum() * 100.0

    non_cash = positions[positions["stock"] != CASH_LABEL] if not positions.empty else positions
    total_market = float(non_cash["market_value"].dropna().sum()) if not non_cash.empty else 0.0
    total_liq = float(non_cash["liquidation_value"].dropna().sum()) if not non_cash.empty else 0.0
    total_cost = float(non_cash["cost"].sum()) if not non_cash.empty else 0.0
    unrealized = total_liq - total_cost
    unrealized_pct = unrealized / total_cost * 100.0 if total_cost else 0.0
    all_exposures = build_expanded_exposure(positions)
    exposures = all_exposures[:50]
    return {
        "positions": positions,
        "total_market": total_market,
        "total_liq": total_liq,
        "cash_twd": cash_amount,
        "total_cost": total_cost,
        "unrealized": unrealized,
        "unrealized_pct": unrealized_pct,
        "holding_count": len(all_exposures),
        "exposures": exposures,
        "tsmc_proxy": positions.attrs.get("tsmc_proxy") if hasattr(positions, "attrs") else None,
        "tsmc_data_mode": positions.attrs.get("tsmc_data_mode") if hasattr(positions, "attrs") else None,
    }


def build_master_text(snapshot, quote_cache=None):
    lines = [
        "吳大師持股",
        f"目前淨值(扣費稅)：{_fmt_money(snapshot['total_liq'])}",
        f"現金：{_fmt_money(snapshot.get('cash_twd', 0))}",
        f"總成本：{_fmt_money(snapshot['total_cost'])}",
        f"未實損益：{_fmt_money(snapshot['unrealized'])} ({snapshot['unrealized_pct']:+.2f}%)",
        f"展開後庫存：{snapshot['holding_count']} 檔"
    ]

    if quote_cache:
        composite = quote_cache.get("composite_move_pct")
        if quote_cache.get("composite_mode") == "live" and composite is not None:
            comp_text = f"{composite:+.2f}%"
            composite_label = f"即時加權 ({quote_cache.get('composite_country_scope', '展開')})"
            lines.append(f"- {composite_label}：{comp_text}")
            composite_count = quote_cache.get("composite_holding_count", 0)
            composite_weight = quote_cache.get("composite_weight_pct")
            weight_text = "--" if composite_weight is None else f"{float(composite_weight):.1f}%"
            lines.append(f"- 交易中{composite_count}檔（權重{weight_text}）")
            
        counts = quote_cache.get("counts", {})
        lines.append(f"- 上漲 {counts.get('up', 0)} / 下跌 {counts.get('down', 0)} / 無變動 {counts.get('flat', 0)}")
        
    lines.append("展開明細：前50大，依權重排序")
    return "\n".join(lines)


def _master_quote_cache(snapshot, rows):
    valid_quote_times = [row.get("quote_time_utc") for row in rows if row.get("quote_time_utc")]
    up = down = flat = missing = 0
    live_weighted_sum = 0.0
    live_weight_sum = 0.0
    live_count = 0
    holdings = []
    for row in rows:
        change = row.get("day_change_pct")
        weight = row.get("weight_pct")
        is_live = _is_live_market_session(row.get("market_session"))
        if change is None or pd.isna(change):
            missing += 1
        else:
            if change > 0:
                up += 1
            elif change < 0:
                down += 1
            else:
                flat += 1
            if is_live and weight is not None and not pd.isna(weight):
                live_weighted_sum += float(weight) * float(change)
                live_weight_sum += float(weight)
                live_count += 1
        holdings.append({
            "id": row.get("code"),
            "name": row.get("name"),
            "weight_pct": row.get("weight_pct"),
            "country": row.get("country"),
            "day_change_pct": change,
            "quote_time_utc": row.get("quote_time_utc"),
            "market_session": row.get("market_session"),
            "is_live_market": is_live,
            "proxy": row.get("proxy"),
            "status": "ok" if change is not None and not pd.isna(change) else "missing",
        })
    unrealized_text = f"{_fmt_money(snapshot['unrealized'])} ({snapshot['unrealized_pct']:+.2f}%)"
    if snapshot["unrealized"] > 0:
        unrealized_color = "RED"
    elif snapshot["unrealized"] < 0:
        unrealized_color = "GREEN"
    else:
        unrealized_color = "MUTED"

    return {
        "ticker": "MASTER",
        "display_ticker": "吳大師",
        "display_name": "展開持股前50大",
        "subtitle": (
            f"淨值 {_fmt_money(snapshot['total_liq'])}｜"
            f"未實 {unrealized_text}｜"
            f"展開後 {snapshot['holding_count']} 檔"
        ),
        "subtitle_parts": [
            {"text": f"淨值 {_fmt_money(snapshot['total_liq'])}｜未實 ", "color": "MUTED"},
            {"text": unrealized_text, "color": unrealized_color},
            {"text": f"｜展開後 {snapshot['holding_count']} 檔", "color": "MUTED"},
        ],
        "sort_note": "依展開後權重排序",
        "holdings_date": datetime.now(timezone.utc).date().isoformat(),
        "generated_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "etf_refresh_utc": max(valid_quote_times) if valid_quote_times else datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "tsmc_proxy": snapshot.get("tsmc_proxy"),
        "tsmc_data_mode": snapshot.get("tsmc_data_mode"),
        "newest_quote_utc": max(valid_quote_times) if valid_quote_times else None,
        "oldest_quote_utc": min(valid_quote_times) if valid_quote_times else None,
        "composite_move_pct": live_weighted_sum / live_weight_sum if live_weight_sum else None,
        "composite_mode": "live" if live_weight_sum else "none",
        "composite_country_scope": "展開",
        "composite_holding_count": live_count,
        "composite_weight_pct": live_weight_sum,
        "counts": {
            "total": len(rows),
            "up": up,
            "down": down,
            "flat": flat,
            "missing": missing,
        },
        "holdings": holdings,
    }


def generate_master_quote_card(limit=50):
    snapshot = load_master_snapshot()
    rows = snapshot["exposures"][:limit]
    quote_cache = _master_quote_cache(snapshot, rows)
    output_paths = generate_quote_card_from_cache("MASTER", quote_cache, output_prefix="master_holding_top50")
    text = build_master_text(snapshot, quote_cache)
    QUOTE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = {
        "generated_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "text": text,
        "image_files": [path.name for path in output_paths],
        "limit": limit,
        "quote_card_cache": quote_cache,
        "summary": {
            "total_liq": snapshot["total_liq"],
            "cash_twd": snapshot["cash_twd"],
            "total_cost": snapshot["total_cost"],
            "unrealized": snapshot["unrealized"],
            "unrealized_pct": snapshot["unrealized_pct"],
            "holding_count": snapshot["holding_count"],
            "expanded_count": len(snapshot["exposures"]),
        },
    }
    tmp_path = MASTER_CACHE_PATH.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(MASTER_CACHE_PATH)
    return text, output_paths


def load_cached_master_quote_card():
    with MASTER_CACHE_PATH.open("r", encoding="utf-8") as fh:
        cache = json.load(fh)
    cache_mtime = MASTER_CACHE_PATH.stat().st_mtime
    source_paths = [
        MASTER_PATH,
        *DATA_DIR.glob("etf_*_history.json"),
        *DATA_DIR.glob("passive_*_history.json"),
        *QUOTE_CACHE_DIR.glob("etf_*_quotes.json"),
    ]
    newest_source_mtime = max(
        (path.stat().st_mtime for path in source_paths if path.exists()),
        default=0,
    )
    if newest_source_mtime > cache_mtime:
        raise FileNotFoundError("Master holding cache is stale")
    image_files = cache.get("image_files") or []
    output_paths = [IMAGE_DIR / filename for filename in image_files]
    if not cache.get("text") or not output_paths or any(not path.exists() for path in output_paths):
        raise FileNotFoundError("Master holding cache is incomplete")
    return cache["text"], output_paths, cache


if __name__ == "__main__":
    text, paths = generate_master_quote_card()
    print(text)
    for path in paths:
        print(path)
