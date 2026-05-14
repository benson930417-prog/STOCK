import argparse
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from zoneinfo import ZoneInfo

import requests


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
QUOTE_CACHE_DIR = DATA_DIR / "quote_cache"

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
HEADERS = {"User-Agent": "Mozilla/5.0"}


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_latest_holdings(ticker):
    if ticker == "0050":
        history_path = DATA_DIR / "passive_0050_history.json"
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
    if ticker == "0050":
        log_path = DATA_DIR / "passive_0050_log.json"
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

    if market in {"US", "USA", "NASDAQ", "NYSE", "AMEX"}:
        country = "US"
    elif market in {"HK", "HKG"}:
        country = "HK"
    elif market in {"JP", "JT", "JPN"}:
        country = "JP"
    elif market in {"TW", "TT", "TWO"}:
        country = "TW"
    elif market:
        country = market[:2]

    symbol = symbol.replace("/", "-")

    if "." in symbol:
        suffix = symbol.rsplit(".", 1)[-1]
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

    if symbol.isdigit():
        return f"{symbol}.TW", "TW"

    return symbol, country or "US"


def _market_time(meta, key):
    value = meta.get(key)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _session_for_us_timestamp(timestamp):
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


def _exchange_session(country):
    if country == "TW":
        now = datetime.now(ZoneInfo("Asia/Taipei"))
        minutes = now.hour * 60 + now.minute
        if now.weekday() < 5 and 9 * 60 <= minutes < 13 * 60 + 30:
            return "REG"
        return "CLOSE"

    if country == "JP":
        now = datetime.now(ZoneInfo("Asia/Tokyo"))
        minutes = now.hour * 60 + now.minute
        if now.weekday() < 5 and 9 * 60 <= minutes < 15 * 60 + 30:
            return "REG"
        return "CLOSE"

    if country == "HK":
        now = datetime.now(ZoneInfo("Asia/Hong_Kong"))
        minutes = now.hour * 60 + now.minute
        if now.weekday() < 5 and 9 * 60 + 30 <= minutes < 16 * 60:
            return "REG"
        return "CLOSE"

    return "REG"


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
        if price is not None and timestamp is not None:
            return price, timestamp, _exchange_session(country)
        return None, None, None


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

        if country == "US" and valid_points:
            quote_time, price = valid_points[-1]
            session = _session_for_us_timestamp(quote_time)
        elif price is None or quote_time is None:
            if valid_points:
                quote_time, price = valid_points[-1]
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
    quote = _fetch_yahoo_chart_quote(symbol, country=country, timeout=timeout)
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


def build_cache(ticker):
    holdings_date, latest_data, holdings = load_latest_holdings(ticker)
    etf_refresh_utc = load_etf_refresh_time(ticker)

    normalized = []
    for holding in holdings:
        yahoo_symbol, country = normalize_yahoo_symbol(holding.get("id"))
        normalized.append((holding, yahoo_symbol, country))

    quotes = fetch_yahoo_quotes([(item[1], item[2]) for item in normalized])

    rows = []
    valid_quote_times = []
    weighted_move_sum = 0.0
    valid_weight_sum = 0.0
    up_count = down_count = flat_count = missing_count = 0

    for holding, yahoo_symbol, country in normalized:
        quote = quotes.get(yahoo_symbol) if yahoo_symbol else None
        weight_pct = holding.get("weight_pct")
        day_change_pct = None
        quote_time_utc = None
        status = "missing"

        if quote and not quote.get("error"):
            day_change_pct = quote.get("regularMarketChangePercent")
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
                include_in_composite = ticker != "00997A" or country == "US"
                if weight_pct is not None and include_in_composite:
                    weighted_move_sum += float(weight_pct) * float(day_change_pct)
                    valid_weight_sum += float(weight_pct)
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
            "market_session": quote.get("marketSession") if quote else None,
            "status": status,
            "error": quote.get("error") if quote else "missing yahoo symbol",
        })

    composite_move_pct = None
    if valid_weight_sum:
        composite_move_pct = weighted_move_sum / valid_weight_sum

    return {
        "ticker": ticker,
        "holdings_date": holdings_date,
        "generated_utc": utc_now_iso(),
        "etf_refresh_utc": etf_refresh_utc,
        "newest_quote_utc": max(valid_quote_times) if valid_quote_times else None,
        "oldest_quote_utc": min(valid_quote_times) if valid_quote_times else None,
        "composite_move_pct": composite_move_pct,
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
    os.replace(tmp_path, path)


def monitor(ticker, interval):
    cache_path = QUOTE_CACHE_DIR / f"etf_{ticker}_quotes.json"
    while True:
        try:
            cache = build_cache(ticker)
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
