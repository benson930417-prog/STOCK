import argparse
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from zoneinfo import ZoneInfo

import requests


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
QUOTE_CACHE_DIR = DATA_DIR / "quote_cache"

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
TWSE_STOCKINFO_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
TRADINGVIEW_SCAN_URL = "https://scanner.tradingview.com/futures/scan"
HEADERS = {"User-Agent": "Mozilla/5.0"}
LIVE_MARKET_SESSIONS = {"PRE", "REG", "POST", "FUT_NIGHT"}
COUNTRY_LABELS = {
    "TW": "台",
    "US": "美",
    "JP": "日",
    "HK": "港",
    "TSMC_FUT": "台積電期貨",
}
COUNTRY_ORDER = ["TW", "JP", "TSMC_FUT", "US", "HK"]
TSMC_PROXY_SYMBOL = "TAIFEX:QFF1!"
TSMC_PROXY_TARGETS = {"2330", "2330.TW"}
PASSIVE_ETF_TICKERS = {"0050", "00830", "00878", "00891", "009805", "009820"}
TRADINGVIEW_DELAY_SECONDS = 900
YAHOO_TWSE_DELAY_SECONDS = 20 * 60
TSMC_NIGHT_FUTURES_OFFICIAL_OPEN_MINUTES = 17 * 60 + 25
TSMC_NIGHT_FUTURES_OFFICIAL_CLOSE_MINUTES = 5 * 60
TW_REGULAR_OFFICIAL_OPEN_MINUTES = 9 * 60
TW_REGULAR_OFFICIAL_CLOSE_MINUTES = 13 * 60 + 30
JP_REGULAR_OFFICIAL_OPEN_MINUTES = 9 * 60
JP_REGULAR_OFFICIAL_CLOSE_MINUTES = 15 * 60 + 30
HK_REGULAR_OFFICIAL_OPEN_MINUTES = 9 * 60 + 30
HK_REGULAR_OFFICIAL_BREAK_START_MINUTES = 12 * 60
HK_REGULAR_OFFICIAL_BREAK_END_MINUTES = 13 * 60
HK_REGULAR_OFFICIAL_CLOSE_MINUTES = 16 * 60
JP_REGULAR_OFFICIAL_BREAK_START_MINUTES = 11 * 60 + 30
JP_REGULAR_OFFICIAL_BREAK_END_MINUTES = 12 * 60 + 30
KR_REGULAR_OFFICIAL_OPEN_MINUTES = 9 * 60
KR_REGULAR_OFFICIAL_CLOSE_MINUTES = 15 * 60 + 30
CN_REGULAR_OFFICIAL_OPEN_MINUTES = 9 * 60 + 30
CN_REGULAR_OFFICIAL_BREAK_START_MINUTES = 11 * 60 + 30
CN_REGULAR_OFFICIAL_BREAK_END_MINUTES = 13 * 60
CN_REGULAR_OFFICIAL_CLOSE_MINUTES = 15 * 60
EU_REGULAR_OFFICIAL_OPEN_MINUTES = 9 * 60
EU_REGULAR_OFFICIAL_CLOSE_MINUTES = 17 * 60 + 30
TSMC_PROXY_CACHE_PATH = QUOTE_CACHE_DIR / "tsmc_qff_proxy.json"


def _delayed_minutes(official_minutes, delay_seconds):
    return official_minutes + delay_seconds // 60


TSMC_NIGHT_FUTURES_START_MINUTES = _delayed_minutes(
    TSMC_NIGHT_FUTURES_OFFICIAL_OPEN_MINUTES,
    TRADINGVIEW_DELAY_SECONDS,
)
TSMC_NIGHT_FUTURES_CLOSE_MINUTES = _delayed_minutes(
    TSMC_NIGHT_FUTURES_OFFICIAL_CLOSE_MINUTES,
    TRADINGVIEW_DELAY_SECONDS,
)
TW_REGULAR_DATA_OPEN_MINUTES = _delayed_minutes(TW_REGULAR_OFFICIAL_OPEN_MINUTES, YAHOO_TWSE_DELAY_SECONDS)
TW_REGULAR_DATA_CLOSE_MINUTES = _delayed_minutes(TW_REGULAR_OFFICIAL_CLOSE_MINUTES, YAHOO_TWSE_DELAY_SECONDS)


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _is_live_market_session(session):
    return str(session or "").upper() in LIVE_MARKET_SESSIONS


def _country_scope_label(countries):
    normalized = {str(country or "").upper() for country in countries if country}
    ordered = [country for country in COUNTRY_ORDER if country in normalized]
    ordered.extend(sorted(normalized - set(ordered)))
    labels = [COUNTRY_LABELS.get(country, country) for country in ordered]
    if "TSMC_FUT" in normalized:
        return "+".join(labels) or "--"
    return "".join(labels) or "--"


def _tsmc_night_futures_collection_session(now=None):
    now = now or datetime.now(ZoneInfo("Asia/Taipei"))
    minutes = now.hour * 60 + now.minute
    # TradingView is delayed, so start after delay headroom and collect final prints until 05:15.
    if now.weekday() < 5 and minutes >= TSMC_NIGHT_FUTURES_START_MINUTES:
        return "FUT_NIGHT"
    if 1 <= now.weekday() <= 5 and minutes < 5 * 60:
        return "FUT_NIGHT"
    if 1 <= now.weekday() <= 5 and minutes < TSMC_NIGHT_FUTURES_CLOSE_MINUTES:
        return "FUT_NIGHT_CLOSE"
    return None


def _tsmc_night_futures_session(now=None):
    now = now or datetime.now(ZoneInfo("Asia/Taipei"))
    collection_session = _tsmc_night_futures_collection_session(now)
    if collection_session:
        return collection_session
    minutes = now.hour * 60 + now.minute
    if 1 <= now.weekday() <= 5 and minutes < TW_REGULAR_DATA_OPEN_MINUTES:
        return "FUT_NIGHT_CLOSE"
    return None


def _before_next_tw_regular_data_open(now=None):
    now = now or datetime.now(ZoneInfo("Asia/Taipei"))
    minutes = now.hour * 60 + now.minute
    if now.weekday() < 5:
        return minutes < TW_REGULAR_OFFICIAL_OPEN_MINUTES
    return True


def _tsmc_data_mode(now=None):
    now = now or datetime.now(ZoneInfo("Asia/Taipei"))
    minutes = now.hour * 60 + now.minute
    collection_session = _tsmc_night_futures_collection_session(now)
    if collection_session == "FUT_NIGHT":
        return "FUTURES_FETCH"
    if collection_session == "FUT_NIGHT_CLOSE":
        return "FUTURES_CLOSE_FETCH"
    if now.weekday() < 5 and TSMC_NIGHT_FUTURES_OFFICIAL_OPEN_MINUTES <= minutes < TSMC_NIGHT_FUTURES_START_MINUTES:
        return "FUTURES_PENDING"
    if _before_next_tw_regular_data_open(now):
        return "FUTURES_CLOSE_FETCH"
    return "TW_NORMAL"


def _latest_tsmc_night_futures_close_available_time(now=None):
    now = now or datetime.now(ZoneInfo("Asia/Taipei"))
    close_minutes = TSMC_NIGHT_FUTURES_OFFICIAL_CLOSE_MINUTES + TRADINGVIEW_DELAY_SECONDS // 60
    close_hour, close_minute = divmod(close_minutes, 60)
    candidate = now.replace(hour=close_hour, minute=close_minute, second=0, microsecond=0)
    if now < candidate:
        candidate -= timedelta(days=1)
    while candidate.weekday() not in {1, 2, 3, 4, 5}:
        candidate -= timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def _is_tsmc_night_futures_session(now=None):
    return _tsmc_night_futures_session(now) is not None


def _fetch_tsmc_night_futures_proxy(timeout=10, include_inactive=False, mode=None):
    mode = mode or _tsmc_data_mode()
    if mode not in {"FUTURES_FETCH", "FUTURES_CLOSE_FETCH"}:
        return {
            "active": False,
            "reason": "outside_tsmc_night_futures_window",
        } if include_inactive else None
    proxy_session = "FUT_NIGHT" if mode == "FUTURES_FETCH" else "FUT_NIGHT_CLOSE"
    payload = {
        "symbols": {"tickers": [TSMC_PROXY_SYMBOL], "query": {"types": []}},
        "columns": [
            "name",
            "close",
            "change",
            "change_abs",
            "volume",
            "update_mode",
            "pricescale",
            "minmov",
            "description",
            "exchange",
        ],
    }
    try:
        res = requests.post(TRADINGVIEW_SCAN_URL, json=payload, headers=HEADERS, timeout=timeout)
        res.raise_for_status()
        data = res.json().get("data") or []
        if not data:
            return None
        values = data[0].get("d") or []
        price = values[1] if len(values) > 1 else None
        if price is None:
            return None
        quote_time = datetime.now(timezone.utc) - timedelta(seconds=TRADINGVIEW_DELAY_SECONDS)
        if mode == "FUTURES_CLOSE_FETCH":
            quote_time = _latest_tsmc_night_futures_close_available_time()
        proxy = {
            "active": True,
            "proxy_symbol": TSMC_PROXY_SYMBOL,
            "proxy_name": values[0] if len(values) > 0 else "QFF1!",
            "price": float(price),
            "tradingview_change_pct": values[2] if len(values) > 2 else None,
            "tradingview_change_abs": values[3] if len(values) > 3 else None,
            "volume": values[4] if len(values) > 4 else None,
            "update_mode": values[5] if len(values) > 5 else None,
            "delay_seconds": TRADINGVIEW_DELAY_SECONDS,
            "market_session": proxy_session,
            "quote_time": int(quote_time.timestamp()),
            "quote_time_utc": quote_time.isoformat().replace("+00:00", "Z"),
        }
        _write_tsmc_proxy_cache(proxy)
        return proxy
    except Exception as exc:
        return {
            "active": False,
            "reason": "fetch_failed",
            "error": str(exc),
        } if include_inactive else None


def _apply_tsmc_night_futures_proxy(quote, yahoo_symbol, proxy):
    if not proxy or not quote or quote.get("error"):
        return quote
    if str(yahoo_symbol or "").upper() not in TSMC_PROXY_TARGETS:
        return quote
    if proxy.get("data_mode") == "FUTURES_PENDING":
        proxied = dict(quote)
        proxied["regularMarketPrice"] = None
        proxied["regularMarketTime"] = None
        proxied["regularMarketChangePercent"] = None
        proxied["marketSession"] = "FUT_NIGHT"
        proxied["composite_scope"] = "TSMC_FUT"
        proxied["proxy"] = {
            "source": "tsmc_night_futures",
            "symbol": TSMC_PROXY_SYMBOL,
            "name": "QFF1!",
            "baseline_symbol": "2330.TW",
            "delay_seconds": TRADINGVIEW_DELAY_SECONDS,
            "status": "waiting_first_delayed_print",
        }
        return proxied
    if not proxy.get("active"):
        return quote
    try:
        proxy_price = float(proxy["price"])
    except (TypeError, ValueError, KeyError):
        return quote
    try:
        tv_change_pct = float(proxy["tradingview_change_pct"])
    except (TypeError, ValueError, KeyError):
        tv_change_pct = None

    proxied = dict(quote)
    proxied["regularMarketPrice"] = proxy_price
    proxied["previousClose"] = None
    proxied["regularMarketTime"] = proxy["quote_time"]
    proxied["regularMarketChangePercent"] = tv_change_pct
    proxied["marketSession"] = proxy.get("market_session") or "FUT_NIGHT"
    proxied["composite_scope"] = "TSMC_FUT"
    proxied["proxy"] = {
        "source": "tsmc_night_futures",
        "symbol": proxy["proxy_symbol"],
        "name": proxy["proxy_name"],
        "change_source": "tradingview_print",
        "tradingview_change_pct": tv_change_pct,
        "tradingview_change_abs": proxy.get("tradingview_change_abs"),
        "delay_seconds": proxy["delay_seconds"],
        "volume": proxy.get("volume"),
        "update_mode": proxy.get("update_mode"),
    }
    return proxied


def _tsmc_proxy_cache_status(has_target, proxy):
    if not has_target:
        return {
            "active": False,
            "reason": "no_2330_holding",
            "window_taipei": "17:40-05:15",
        }
    if not proxy:
        return {
            "active": False,
            "reason": "not_fetched",
            "window_taipei": "17:40-05:15",
        }
    keys = [
        "active",
        "data_mode",
        "reason",
        "error",
        "proxy_symbol",
        "proxy_name",
        "price",
        "market_session",
        "quote_time",
        "quote_time_utc",
        "delay_seconds",
        "update_mode",
        "volume",
        "tradingview_change_pct",
        "tradingview_change_abs",
    ]
    status = {key: proxy.get(key) for key in keys if key in proxy}
    status["window_taipei"] = "17:40-05:15"
    status["display_until_taipei"] = "09:00"
    return status


def _write_tsmc_proxy_cache(proxy):
    if not proxy or not proxy.get("active"):
        return
    TSMC_PROXY_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=TSMC_PROXY_CACHE_PATH.parent, delete=False) as tmp:
        json.dump(proxy, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, TSMC_PROXY_CACHE_PATH)


def _read_tsmc_proxy_cache():
    if not TSMC_PROXY_CACHE_PATH.exists():
        return None
    try:
        return json.loads(TSMC_PROXY_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def _parse_utc_timestamp(value):
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(text).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _proxy_status_from_cached_row(row):
    proxy = row.get("proxy") or {}
    if not proxy or row.get("price") is None:
        return None
    quote_time_utc = row.get("quote_time_utc")
    quote_dt = _parse_utc_timestamp(quote_time_utc)
    return {
        "active": True,
        "proxy_symbol": proxy.get("symbol") or TSMC_PROXY_SYMBOL,
        "proxy_name": proxy.get("name") or "QFF1!",
        "price": row.get("price"),
        "volume": proxy.get("volume"),
        "update_mode": proxy.get("update_mode"),
        "delay_seconds": proxy.get("delay_seconds") or TRADINGVIEW_DELAY_SECONDS,
        "market_session": "FUT_NIGHT_CLOSE",
        "quote_time": int(quote_dt.timestamp()) if quote_dt else None,
        "quote_time_utc": quote_time_utc,
        "tradingview_change_pct": proxy.get("tradingview_change_pct", row.get("day_change_pct")),
        "tradingview_change_abs": proxy.get("tradingview_change_abs"),
    }


def _cached_tsmc_proxy_for_display(previous_cache, now=None):
    now = now or datetime.now(ZoneInfo("Asia/Taipei"))
    if _tsmc_data_mode(now) != "FUTURES_CLOSE_FETCH":
        return None
    proxy = previous_cache.get("tsmc_proxy") if previous_cache else None
    if not proxy or not proxy.get("active"):
        for row in (previous_cache or {}).get("holdings", []):
            if str(row.get("yahoo_symbol") or row.get("id") or "").upper() in TSMC_PROXY_TARGETS:
                proxy = _proxy_status_from_cached_row(row)
                break
    if not proxy or not proxy.get("active"):
        proxy = _read_tsmc_proxy_cache()
    if not proxy or not proxy.get("active") or proxy.get("price") is None:
        return None

    quote_dt = _parse_utc_timestamp(proxy.get("quote_time_utc"))
    if not quote_dt:
        return None
    quote_taipei = quote_dt.astimezone(ZoneInfo("Asia/Taipei"))
    if quote_taipei > now:
        return None
    if now - quote_taipei > timedelta(days=4):
        return None

    cached_proxy = dict(proxy)
    cached_proxy["active"] = True
    cached_proxy["market_session"] = "FUT_NIGHT_CLOSE"
    cached_proxy["quote_time"] = cached_proxy.get("quote_time") or int(quote_dt.timestamp())
    cached_proxy["reason"] = "cached_until_tw_open"
    return cached_proxy


def _select_tsmc_proxy(mode, previous_cache=None):
    if mode == "TW_NORMAL":
        return {
            "active": False,
            "reason": "tw_normal_selected",
            "data_mode": mode,
        }
    if mode == "FUTURES_PENDING":
        return {
            "active": False,
            "reason": "waiting_first_delayed_print",
            "data_mode": mode,
        }
    if mode == "FUTURES_FETCH":
        proxy = _fetch_tsmc_night_futures_proxy(include_inactive=True, mode=mode)
        if proxy:
            proxy["data_mode"] = mode
        return proxy
    if mode == "FUTURES_CLOSE_FETCH":
        proxy = _fetch_tsmc_night_futures_proxy(include_inactive=True, mode=mode)
        if proxy and proxy.get("active"):
            proxy["data_mode"] = mode
            return proxy
        cached_proxy = _cached_tsmc_proxy_for_display(previous_cache)
        if cached_proxy:
            cached_proxy["data_mode"] = "FUTURES_CLOSE_CACHE"
            return cached_proxy
        if proxy:
            proxy["data_mode"] = mode
            return proxy
    return {
        "active": False,
        "reason": "no_tsmc_data_mode",
        "data_mode": mode,
    }


def load_latest_holdings(ticker):
    if ticker in PASSIVE_ETF_TICKERS:
        history_path = DATA_DIR / f"passive_{ticker}_history.json"
    else:
        history_path = DATA_DIR / f"etf_{ticker}_history.json"
    if not history_path.exists():
        raise FileNotFoundError(f"Missing history file: {history_path}")

    with history_path.open("r", encoding="utf-8") as fh:
        history = json.load(fh)

    if not history:
        raise ValueError(f"No history data in {history_path}")

    latest_date = max(history.keys())
    latest_data = history[latest_date]
    holdings = latest_data.get("holdings", [])
    if not holdings:
        raise ValueError(f"No holdings for {ticker} on {latest_date}")

    return latest_date, latest_data, holdings


def load_etf_refresh_time(ticker):
    if ticker in PASSIVE_ETF_TICKERS:
        log_path = DATA_DIR / f"passive_{ticker}_log.json"
    else:
        log_path = DATA_DIR / f"etf_{ticker}_log.json"
    if not log_path.exists():
        return None

    try:
        with log_path.open("r", encoding="utf-8") as fh:
            log_data = json.load(fh)
        return log_data.get("last_updated_utc") or log_data.get("last_checked_utc")
    except Exception:
        return None


def normalize_yahoo_symbol(raw_id):
    raw = str(raw_id or "").strip().upper()
    raw = re.sub(r"\s+", " ", raw)
    if not raw:
        return None, None

    # Bloomberg-like forms from international ETF sheets: "NVDA US", "BRK/B US".
    parts = raw.split(" ")
    symbol = parts[0]
    market = parts[1] if len(parts) > 1 else ""
    country = None

    if market in {"US", "USA", "NASDAQ", "NYSE", "AMEX", "UN", "UW", "UQ", "UP", "UR"}:
        country = "US"
    elif market in {"HK", "HKG"}:
        country = "HK"
    elif market in {"JP", "JT", "JPN"}:
        country = "JP"
    elif market in {"TW", "TT", "TWO"}:
        country = "TW"
    elif market in {"KS", "KR"}:
        country = "KR"
    elif market in {"KQ"}:
        country = "KR"
    elif market in {"GY", "GR", "DE"}:
        country = "DE"
    elif market in {"FP", "FR"}:
        country = "FR"
    elif market in {"CH", "CN", "SS", "SZ"}:
        country = "CN"
    elif market:
        country = market[:2]

    symbol = symbol.replace("/", "-")

    if "." in symbol:
        base, suffix = symbol.rsplit(".", 1)
        if suffix in {"US", "USA"}:
            return base, "US"
        country = country or {
            "TW": "TW",
            "TWO": "TW",
            "HK": "HK",
            "T": "JP",
        }.get(suffix, suffix[:2])
        return symbol, country

    if country == "HK":
        if symbol.isdigit():
            symbol = symbol.zfill(4)
        return f"{symbol}.HK", "HK"

    if country == "JP":
        return f"{symbol}.T", "JP"

    if country == "TW":
        suffix = ".TWO" if market == "TWO" else ".TW"
        return f"{symbol}{suffix}", "TW"

    if country == "KR":
        suffix = ".KQ" if market == "KQ" else ".KS"
        return f"{symbol}{suffix}", "KR"

    if country == "DE":
        return f"{symbol}.DE", "DE"

    if country == "FR":
        return f"{symbol}.PA", "FR"

    if country == "CN":
        if market == "SZ" or symbol.startswith(("0", "3")):
            return f"{symbol}.SZ", "CN"
        return f"{symbol}.SS", "CN"

    if symbol.isdigit():
        return f"{symbol}.TW", "TW"

    return symbol, country or "US"


def _market_time(meta, key):
    value = meta.get(key)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _to_quote_float(value):
    if value is None:
        return None
    text = str(value).replace(",", "").strip()
    if not text or text in {"-", "--", "NaN"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _tw_symbol_code(symbol):
    raw = str(symbol or "").strip().upper()
    if raw.endswith(".TWO"):
        return raw[:-4], "otc"
    if raw.endswith(".TW"):
        return raw[:-3], "tse"
    return raw, None


def _twse_quote_timestamp(row):
    date_text = str(row.get("d") or "").strip()
    time_text = str(row.get("t") or "").strip()
    if len(date_text) != 8 or not time_text:
        return None
    try:
        dt = datetime.strptime(f"{date_text} {time_text}", "%Y%m%d %H:%M:%S")
        return int(dt.replace(tzinfo=ZoneInfo("Asia/Taipei")).timestamp())
    except ValueError:
        return None


def _fetch_twse_realtime_quote(symbol, country=None, timeout=5):
    if country != "TW":
        return None

    code, preferred_exchange = _tw_symbol_code(symbol)
    if not code:
        return None

    exchanges = [preferred_exchange] if preferred_exchange else ["tse", "otc"]
    exchanges.extend(exchange for exchange in ["tse", "otc"] if exchange not in exchanges)

    for exchange in exchanges:
        ex_ch = f"{exchange}_{code}.tw"
        try:
            res = requests.get(
                TWSE_STOCKINFO_URL,
                params={"ex_ch": ex_ch, "json": "1", "delay": "0", "_": int(time.time() * 1000)},
                headers={**HEADERS, "Referer": "https://mis.twse.com.tw/stock/fibest.jsp"},
                timeout=timeout,
            )
            res.raise_for_status()
            payload = res.json()
        except Exception:
            continue

        rows = payload.get("msgArray") or []
        if not rows:
            continue

        row = rows[0]
        price = _to_quote_float(row.get("z"))
        previous = _to_quote_float(row.get("y"))
        quote_time = _twse_quote_timestamp(row)
        if price is None and previous is None:
            continue

        session = _exchange_session("TW")
        if quote_time and session == "REG" and not _quote_belongs_to_current_regular_session("TW", quote_time):
            continue

        change_pct = None
        if price is not None and previous:
            change_pct = (price - previous) / previous * 100.0

        return {
            "symbol": symbol,
            "regularMarketPrice": price,
            "previousClose": previous,
            "regularMarketTime": quote_time,
            "regularMarketChangePercent": change_pct,
            "marketSession": session or "CLOSE",
            "currency": "TWD",
            "source": "twse_mis",
        }

    return None


# ─── US trading-day detection via Yahoo heartbeat ──────────────────────────
# Instead of maintaining a hardcoded holiday calendar (which needs yearly
# updates as new holidays are added — Juneteenth in 2022, etc.), check
# whether NVDA shows any market activity today. NVDA is the most-traded
# US stock with continuous pre/regular/post sessions every trading day,
# so its quote timestamps are a reliable heartbeat:
#   • Today's pre/regular/post timestamp exists → today IS a trading day
#   • Only timestamps from previous days → today is holiday/weekend
# Cached per-day so we don't hit Yahoo on every quote refresh.

# Cache structure: {date: us_date, result: bool, fetched_at: utc_datetime}
_US_TRADING_CACHE: dict = {}
# Re-check after this long when cached result is False — handles the edge
# case where the bot starts at 3 AM ET (before pre-market opens at 4 AM)
# and would otherwise cache "closed" for the rest of the day.
_NEGATIVE_CACHE_TTL = timedelta(minutes=15)


def _is_us_trading_today():
    """Return True if NVDA shows any timestamp from today's US date.
    Returns False on holidays/weekends. Returns None if we can't reach
    Yahoo (caller should treat as "unknown — fail open / assume open")."""
    now_et = datetime.now(ZoneInfo("America/New_York"))
    today_us = now_et.date()

    # Weekend short-circuit (no Yahoo call needed)
    if now_et.weekday() >= 5:
        return False

    cached_date   = _US_TRADING_CACHE.get("date")
    cached_result = _US_TRADING_CACHE.get("result")
    cached_at     = _US_TRADING_CACHE.get("fetched_at")

    # Positive cache: trusted for the whole day (NVDA can't un-trade)
    if cached_date == today_us and cached_result is True:
        return True
    # Negative cache: trusted briefly — re-check after TTL in case pre-market
    # just opened since our last check (e.g. checked at 3:55 AM, market opens
    # at 4:00 AM, we don't want to keep returning False all day)
    if (cached_date == today_us
        and cached_result is False
        and cached_at
        and (datetime.now(timezone.utc) - cached_at) < _NEGATIVE_CACHE_TTL):
        return False

    # Ask Yahoo for NVDA's current quote
    try:
        res = requests.get(
            YAHOO_CHART_URL.format(symbol="NVDA"),
            params={"range": "1d", "interval": "1m"},
            headers=HEADERS, timeout=10,
        )
        res.raise_for_status()
        result = (res.json().get("chart", {}).get("result") or [None])[0]
        if not result:
            return None
        meta = result.get("meta", {})
        # Any of these timestamps falling on today's US date proves activity
        for field in ("preMarketTime", "regularMarketTime", "postMarketTime"):
            ts = meta.get(field)
            if not ts:
                continue
            try:
                ts_dt = datetime.fromtimestamp(int(ts), ZoneInfo("America/New_York"))
            except (ValueError, OSError):
                continue
            if ts_dt.date() == today_us:
                _US_TRADING_CACHE.update({
                    "date": today_us, "result": True,
                    "fetched_at": datetime.now(timezone.utc),
                })
                return True
        # No activity today on NVDA → today is a holiday / weekend / pre-open
        _US_TRADING_CACHE.update({
            "date": today_us, "result": False,
            "fetched_at": datetime.now(timezone.utc),
        })
        return False
    except Exception as exc:
        print(f"[us-trading-check] NVDA heartbeat failed: {exc}")
        return None    # fail open — caller treats None as "unknown, assume open"


def _is_us_market_closed_today() -> bool:
    """True if US market is DEFINITIVELY closed today (holiday or weekend).
    Returns False on uncertainty so we never accidentally mark a trading
    day as closed."""
    result = _is_us_trading_today()
    if result is None:
        return False    # unknown → fail open (don't risk mislabelling a real session)
    return not result


def _adjusted_market_session(country, raw_session):
    """Override Yahoo's time-based marketSession to 'CLOSE' on US holidays.
    Yahoo's marketSession is purely time-of-day based — at 6 AM ET on
    Memorial Day it still says 'PRE'. We override using a real heartbeat
    check against NVDA's timestamps (see _is_us_trading_today)."""
    if str(country or "").upper() == "US" and _is_us_market_closed_today():
        return "CLOSE"
    return raw_session


def _session_for_us_timestamp(timestamp):
    """Classify a US quote timestamp into PRE/REG/POST/CLOSE by time-of-day.
    Doesn't itself need a holiday check — if Yahoo returned a stale Friday
    timestamp because today is a holiday, this function correctly classifies
    that Friday timestamp as POST_CLOSE. The holiday override only matters
    for Yahoo's marketSession field, which IS time-of-day based and would
    say PRE on a holiday morning. See _adjusted_market_session."""
    try:
        dt = datetime.fromtimestamp(int(timestamp), ZoneInfo("America/New_York"))
    except Exception:
        return "CLOSE"
    minutes = dt.hour * 60 + dt.minute
    if dt.weekday() < 5 and 4 * 60 <= minutes < 9 * 60 + 30:
        return "PRE"
    if dt.weekday() < 5 and 9 * 60 + 30 <= minutes < 16 * 60:
        return "REG"
    if dt.weekday() < 5 and 16 * 60 <= minutes < 20 * 60:
        now_et = datetime.now(ZoneInfo("America/New_York"))
        post_close_end = dt.replace(hour=20, minute=0, second=0, microsecond=0)
        if now_et >= post_close_end:
            return "POST_CLOSE"
        return "POST"
    return "CLOSE"


def _previous_us_regular_close(symbol, quote_time, timeout=10):
    if not quote_time:
        return None
    try:
        quote_date = datetime.fromtimestamp(int(quote_time), ZoneInfo("America/New_York")).date()
        res = requests.get(
            YAHOO_CHART_URL.format(symbol=symbol),
            params={"range": "10d", "interval": "1d"},
            headers=HEADERS,
            timeout=timeout,
        )
        res.raise_for_status()
        payload = res.json()
        result = (payload.get("chart", {}).get("result") or [None])[0]
        if not result:
            return None
        timestamps = result.get("timestamp") or []
        closes = result.get("indicators", {}).get("quote", [{}])[0].get("close", [])
        previous_points = []
        for timestamp, close in zip(timestamps, closes):
            if timestamp is None or close is None:
                continue
            close_date = datetime.fromtimestamp(int(timestamp), ZoneInfo("America/New_York")).date()
            if close_date < quote_date:
                previous_points.append((timestamp, close))
        if previous_points:
            return previous_points[-1][1]
    except Exception:
        return None
    return None


def _session_bounds(dt, start_minutes, end_minutes):
    return (
        dt.replace(hour=start_minutes // 60, minute=start_minutes % 60, second=0, microsecond=0),
        dt.replace(hour=end_minutes // 60, minute=end_minutes % 60, second=0, microsecond=0),
    )


def _regular_session_bounds(country, now=None):
    country = str(country or "").upper()
    if country == "TW":
        now = now or datetime.now(ZoneInfo("Asia/Taipei"))
        if now.weekday() >= 5:
            return None
        start, end = _session_bounds(now, TW_REGULAR_OFFICIAL_OPEN_MINUTES, TW_REGULAR_OFFICIAL_CLOSE_MINUTES)
        if start <= now < end:
            return start, end
        return None

    if country == "JP":
        now = now or datetime.now(ZoneInfo("Asia/Tokyo"))
        if now.weekday() >= 5:
            return None
        morning_start, morning_end = _session_bounds(
            now,
            JP_REGULAR_OFFICIAL_OPEN_MINUTES,
            JP_REGULAR_OFFICIAL_BREAK_START_MINUTES,
        )
        afternoon_start, afternoon_end = _session_bounds(
            now,
            JP_REGULAR_OFFICIAL_BREAK_END_MINUTES,
            JP_REGULAR_OFFICIAL_CLOSE_MINUTES,
        )
        if morning_start <= now < morning_end:
            return morning_start, morning_end
        if afternoon_start <= now < afternoon_end:
            return afternoon_start, afternoon_end
        return None

    if country == "HK":
        now = now or datetime.now(ZoneInfo("Asia/Hong_Kong"))
        if now.weekday() >= 5:
            return None
        morning_start, morning_end = _session_bounds(
            now,
            HK_REGULAR_OFFICIAL_OPEN_MINUTES,
            HK_REGULAR_OFFICIAL_BREAK_START_MINUTES,
        )
        afternoon_start, afternoon_end = _session_bounds(
            now,
            HK_REGULAR_OFFICIAL_BREAK_END_MINUTES,
            HK_REGULAR_OFFICIAL_CLOSE_MINUTES,
        )
        if morning_start <= now < morning_end:
            return morning_start, morning_end
        if afternoon_start <= now < afternoon_end:
            return afternoon_start, afternoon_end
        return None

    if country == "KR":
        now = now or datetime.now(ZoneInfo("Asia/Seoul"))
        if now.weekday() >= 5:
            return None
        start, end = _session_bounds(now, KR_REGULAR_OFFICIAL_OPEN_MINUTES, KR_REGULAR_OFFICIAL_CLOSE_MINUTES)
        if start <= now < end:
            return start, end
        return None

    if country == "CN":
        now = now or datetime.now(ZoneInfo("Asia/Shanghai"))
        if now.weekday() >= 5:
            return None
        morning_start, morning_end = _session_bounds(
            now,
            CN_REGULAR_OFFICIAL_OPEN_MINUTES,
            CN_REGULAR_OFFICIAL_BREAK_START_MINUTES,
        )
        afternoon_start, afternoon_end = _session_bounds(
            now,
            CN_REGULAR_OFFICIAL_BREAK_END_MINUTES,
            CN_REGULAR_OFFICIAL_CLOSE_MINUTES,
        )
        if morning_start <= now < morning_end:
            return morning_start, morning_end
        if afternoon_start <= now < afternoon_end:
            return afternoon_start, afternoon_end
        return None

    if country == "DE":
        now = now or datetime.now(ZoneInfo("Europe/Berlin"))
        if now.weekday() >= 5:
            return None
        start, end = _session_bounds(now, EU_REGULAR_OFFICIAL_OPEN_MINUTES, EU_REGULAR_OFFICIAL_CLOSE_MINUTES)
        if start <= now < end:
            return start, end
        return None

    if country == "FR":
        now = now or datetime.now(ZoneInfo("Europe/Paris"))
        if now.weekday() >= 5:
            return None
        start, end = _session_bounds(now, EU_REGULAR_OFFICIAL_OPEN_MINUTES, EU_REGULAR_OFFICIAL_CLOSE_MINUTES)
        if start <= now < end:
            return start, end
        return None

    return None


def _exchange_session(country):
    if country in {"TW", "JP", "HK", "KR", "CN", "DE", "FR"}:
        return "REG" if _regular_session_bounds(country) else "CLOSE"
    return "REG"


def _quote_belongs_to_current_regular_session(country, timestamp):
    bounds = _regular_session_bounds(country)
    if not bounds or not timestamp:
        return False
    start, _ = bounds
    try:
        quote_dt = datetime.fromtimestamp(int(timestamp), start.tzinfo)
    except Exception:
        return False
    return quote_dt >= start


def _session_quote_from_meta(meta, country):
    if country == "US":
        now_et = datetime.now(ZoneInfo("America/New_York"))
        minutes = now_et.hour * 60 + now_et.minute
        weekday = now_et.weekday()

        if weekday < 5 and 4 * 60 <= minutes < 9 * 60 + 30:
            session, price, timestamp = "PRE", meta.get("preMarketPrice"), _market_time(meta, "preMarketTime")
        elif weekday < 5 and 9 * 60 + 30 <= minutes < 16 * 60:
            session, price, timestamp = "REG", meta.get("regularMarketPrice"), _market_time(meta, "regularMarketTime")
        elif weekday < 5 and 16 * 60 <= minutes < 20 * 60:
            session, price, timestamp = "POST", meta.get("postMarketPrice"), _market_time(meta, "postMarketTime")
        else:
            session, price, timestamp = "CLOSE", meta.get("regularMarketPrice"), _market_time(meta, "regularMarketTime")

        if price is not None and timestamp is not None:
            return price, timestamp, session

        return None, None, session
    else:
        price = meta.get("regularMarketPrice")
        timestamp = _market_time(meta, "regularMarketTime")
        session = _exchange_session(country)
        if session == "REG" and not _quote_belongs_to_current_regular_session(country, timestamp):
            return None, None, session
        if price is not None and timestamp is not None:
            return price, timestamp, session
        return None, None, session


def _current_us_trading_period(meta, session):
    period_key = {
        "PRE": "pre",
        "REG": "regular",
        "POST": "post",
    }.get(str(session or "").upper())
    if not period_key:
        return None
    period = (meta.get("currentTradingPeriod") or {}).get(period_key)
    if not isinstance(period, dict):
        return None
    start = _market_time(period, "start")
    end = _market_time(period, "end")
    if start is None or end is None:
        return None
    return start, end


def _latest_us_session_point(meta, session, valid_points):
    if not valid_points:
        return None

    bounds = _current_us_trading_period(meta, session)
    if not bounds:
        return valid_points[-1]

    start, end = bounds
    session_points = [
        (timestamp, close)
        for timestamp, close in valid_points
        if start <= int(timestamp) < end
    ]
    return session_points[-1] if session_points else None


def _regular_close_from_meta(meta):
    price = meta.get("regularMarketPrice")
    timestamp = _market_time(meta, "regularMarketTime")
    if price is not None and timestamp is not None:
        return price, timestamp
    return None, None


def _fetch_yahoo_chart_quote(symbol, country=None, timeout=10):
    try:
        params = {"range": "5d", "interval": "1d"}
        if country == "US":
            params = {"range": "1d", "interval": "1m", "includePrePost": "true"}

        res = requests.get(
            YAHOO_CHART_URL.format(symbol=symbol),
            params=params,
            headers=HEADERS,
            timeout=timeout,
        )
        res.raise_for_status()
        payload = res.json()
        chart = payload.get("chart", {})
        error = chart.get("error")
        if error:
            return {"symbol": symbol, "error": str(error)}

        result = chart.get("result") or []
        if not result:
            return {"symbol": symbol, "error": "empty chart result"}

        data = result[0]
        meta = data.get("meta", {})
        timestamps = data.get("timestamp") or []
        closes = data.get("indicators", {}).get("quote", [{}])[0].get("close", [])
        valid_points = [
            (timestamp, close)
            for timestamp, close in zip(timestamps, closes)
            if timestamp is not None and close is not None
        ]

        price, quote_time, session = _session_quote_from_meta(meta, country)

        if country == "US" and (price is None or quote_time is None) and _is_live_market_session(session):
            session_point = _latest_us_session_point(meta, session, valid_points)
            if session_point:
                quote_time, price = session_point
                session = _session_for_us_timestamp(quote_time)
            else:
                price, quote_time = _regular_close_from_meta(meta)
                if price is not None and quote_time is not None:
                    session = "CLOSE"

        if (price is None or quote_time is None) and not _is_live_market_session(session):
            if valid_points:
                quote_time, price = valid_points[-1]
                if country == "US":
                    session = _session_for_us_timestamp(quote_time)
                else:
                    session = "CLOSE"

        previous = None
        if country == "US":
            previous = _previous_us_regular_close(symbol, quote_time, timeout=timeout)
        if len(valid_points) >= 2 and country != "US":
            _, previous = valid_points[-2]
        if previous is None:
            previous = (
                meta.get("regularMarketPreviousClose")
                or meta.get("previousClose")
                or meta.get("chartPreviousClose")
            )

        change_pct = None
        if price is not None and previous:
            change_pct = (float(price) - float(previous)) / float(previous) * 100.0

        return {
            "symbol": symbol,
            "regularMarketPrice": price,
            "previousClose": previous,
            "regularMarketTime": quote_time,
            "regularMarketChangePercent": change_pct,
            "marketSession": session or "CLOSE",
            "currency": meta.get("currency"),
        }
    except Exception as exc:
        return {"symbol": symbol, "error": str(exc)}


def _fetch_yahoo_chart_quote_with_fallback(symbol, country=None, timeout=10):
    tw_quote = _fetch_twse_realtime_quote(symbol, country=country, timeout=min(timeout, 5))
    if tw_quote and tw_quote.get("regularMarketPrice") is not None:
        return tw_quote

    quote = _fetch_yahoo_chart_quote(symbol, country=country, timeout=timeout)
    if (
        tw_quote
        and tw_quote.get("previousClose")
        and quote
        and not quote.get("error")
        and quote.get("regularMarketPrice") is not None
    ):
        price = float(quote["regularMarketPrice"])
        previous = float(tw_quote["previousClose"])
        quote["previousClose"] = previous
        quote["regularMarketChangePercent"] = (price - previous) / previous * 100.0
        if tw_quote.get("regularMarketTime"):
            quote["regularMarketTime"] = tw_quote["regularMarketTime"]
        quote["source"] = "yahoo_price_twse_previous"

    if (
        country == "TW"
        and isinstance(symbol, str)
        and symbol.endswith(".TW")
        and quote.get("error")
    ):
        fallback_symbol = symbol[:-3] + ".TWO"
        fallback_quote = _fetch_yahoo_chart_quote(fallback_symbol, country=country, timeout=timeout)
        if not fallback_quote.get("error"):
            fallback_quote["symbol"] = fallback_symbol
            fallback_quote["fallback_from"] = symbol
            return fallback_quote
    return quote


def fetch_yahoo_quotes(symbol_country_pairs, max_workers=12):
    quotes = {}
    unique_pairs = {}
    for symbol, country in symbol_country_pairs:
        if symbol and symbol not in unique_pairs:
            unique_pairs[symbol] = country
    if not unique_pairs:
        return quotes

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(_fetch_yahoo_chart_quote_with_fallback, symbol, country): symbol
            for symbol, country in unique_pairs.items()
        }
        for future in as_completed(future_map):
            symbol = future_map[future]
            try:
                quotes[symbol] = future.result()
            except Exception as exc:
                quotes[symbol] = {"symbol": symbol, "error": str(exc)}

    return quotes


def build_cache(ticker, previous_cache=None):
    holdings_date, latest_data, holdings = load_latest_holdings(ticker)
    etf_refresh_utc = load_etf_refresh_time(ticker)

    normalized = []
    for holding in holdings:
        yahoo_symbol, country = normalize_yahoo_symbol(holding.get("id"))
        normalized.append((holding, yahoo_symbol, country))

    quotes = fetch_yahoo_quotes([(item[1], item[2]) for item in normalized])
    has_tsmc_proxy_target = any(
        str(yahoo_symbol or "").upper() in TSMC_PROXY_TARGETS for _, yahoo_symbol, _ in normalized
    )
    tsmc_data_mode = _tsmc_data_mode()
    tsmc_proxy = None
    if has_tsmc_proxy_target:
        tsmc_proxy = _select_tsmc_proxy(tsmc_data_mode, previous_cache)

    rows = []
    valid_quote_times = []
    all_weighted_move_sum = 0.0
    all_valid_weight_sum = 0.0
    live_weighted_move_sum = 0.0
    live_valid_weight_sum = 0.0
    all_composite_count = 0
    live_composite_count = 0
    all_composite_countries = set()
    live_composite_countries = set()
    up_count = down_count = flat_count = missing_count = 0

    for holding, yahoo_symbol, country in normalized:
        quote = quotes.get(yahoo_symbol) if yahoo_symbol else None
        quote = _apply_tsmc_night_futures_proxy(quote, yahoo_symbol, tsmc_proxy)
        weight_pct = holding.get("weight_pct")
        day_change_pct = None
        quote_time_utc = None
        market_session = None
        status = "missing"

        if quote and not quote.get("error"):
            day_change_pct = quote.get("regularMarketChangePercent")
            # Override Yahoo's session — it's purely time-based and doesn't
            # know about US market holidays (would say PRE on Memorial Day).
            market_session = _adjusted_market_session(country, quote.get("marketSession"))
            quote_time = quote.get("regularMarketTime")
            if quote_time:
                quote_time_utc = datetime.fromtimestamp(quote_time, timezone.utc).isoformat().replace("+00:00", "Z")
                valid_quote_times.append(quote_time_utc)
            if day_change_pct is not None:
                status = "ok"
                if day_change_pct > 0:
                    up_count += 1
                elif day_change_pct < 0:
                    down_count += 1
                else:
                    flat_count += 1
                if weight_pct is not None:
                    weight = float(weight_pct)
                    move = float(day_change_pct)
                    all_weighted_move_sum += weight * move
                    all_valid_weight_sum += weight
                    all_composite_count += 1
                    all_composite_countries.add(quote.get("composite_scope") or country)
                    if _is_live_market_session(market_session):
                        live_weighted_move_sum += weight * move
                        live_valid_weight_sum += weight
                        live_composite_count += 1
                        live_composite_countries.add(quote.get("composite_scope") or country)
            else:
                missing_count += 1
        else:
            missing_count += 1

        rows.append({
            "id": holding.get("id"),
            "name": holding.get("name"),
            "weight_pct": weight_pct,
            "shares": holding.get("shares"),
            "country": country,
            "yahoo_symbol": quote.get("symbol", yahoo_symbol) if quote else yahoo_symbol,
            "fallback_from_symbol": quote.get("fallback_from") if quote else None,
            "price": quote.get("regularMarketPrice") if quote else None,
            "previous_close": quote.get("previousClose") if quote else None,
            "currency": quote.get("currency") if quote else None,
            "day_change_pct": day_change_pct,
            "quote_time_utc": quote_time_utc,
            "market_session": market_session,
            "is_live_market": _is_live_market_session(market_session),
            "composite_scope": quote.get("composite_scope") if quote else None,
            "proxy": quote.get("proxy") if quote else None,
            "status": status,
            "error": quote.get("error") if quote else "missing yahoo symbol",
        })

    composite_move_pct = None
    composite_mode = "none"
    composite_count = 0
    composite_weight_sum = 0.0
    composite_countries = set()
    if live_valid_weight_sum:
        composite_mode = "live"
        composite_move_pct = live_weighted_move_sum / live_valid_weight_sum
        composite_count = live_composite_count
        composite_weight_sum = live_valid_weight_sum
        composite_countries = live_composite_countries

    return {
        "ticker": ticker,
        "holdings_date": holdings_date,
        "generated_utc": utc_now_iso(),
        "etf_refresh_utc": etf_refresh_utc,
        "tsmc_proxy": _tsmc_proxy_cache_status(has_tsmc_proxy_target, tsmc_proxy),
        "tsmc_data_mode": tsmc_data_mode,
        "newest_quote_utc": max(valid_quote_times) if valid_quote_times else None,
        "oldest_quote_utc": min(valid_quote_times) if valid_quote_times else None,
        "composite_move_pct": composite_move_pct,
        "composite_mode": composite_mode,
        "composite_country_scope": _country_scope_label(composite_countries),
        "composite_holding_count": composite_count,
        "composite_weight_pct": composite_weight_sum,
        "counts": {
            "total": len(rows),
            "up": up_count,
            "down": down_count,
            "flat": flat_count,
            "missing": missing_count,
        },
        "meta": latest_data.get("meta", {}),
        "holdings": rows,
    }


def atomic_write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        json.dump(data, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
        tmp_path = Path(tmp.name)
    tmp_path.chmod(0o644)
    os.replace(tmp_path, path)


def monitor(ticker, interval):
    cache_path = QUOTE_CACHE_DIR / f"etf_{ticker}_quotes.json"
    while True:
        try:
            previous_cache = None
            if cache_path.exists():
                try:
                    previous_cache = json.loads(cache_path.read_text(encoding="utf-8"))
                except Exception:
                    previous_cache = None
            cache = build_cache(ticker, previous_cache=previous_cache)
            atomic_write_json(cache_path, cache)
            counts = cache["counts"]
            print(
                f"{utc_now_iso()} updated {ticker} quote cache: "
                f"{counts['total']} holdings, ok={counts['up'] + counts['down'] + counts['flat']}, "
                f"missing={counts['missing']}",
                flush=True,
            )
        except Exception as exc:
            error_cache = {
                "ticker": ticker,
                "generated_utc": utc_now_iso(),
                "status": "error",
                "error": str(exc),
            }
            atomic_write_json(cache_path, error_cache)
            print(f"{utc_now_iso()} error updating {ticker} quote cache: {exc}", flush=True)

        time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description="Continuously monitor ETF holding quotes into a server-only cache.")
    parser.add_argument("ticker", nargs="?", default="00997A")
    parser.add_argument("--interval", type=int, default=60)
    args = parser.parse_args()

    monitor(args.ticker.upper(), max(args.interval, 10))


if __name__ == "__main__":
    main()
