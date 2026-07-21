from flask import Flask, request, send_from_directory
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    FollowEvent,
    ImageSendMessage,
    JoinEvent,
    MessageAction,
    MessageEvent,
    PostbackEvent,
    QuickReply,
    QuickReplyButton,
    TextMessage,
    TextSendMessage,
)
import time
import os
import sys
import requests
import re
import unicodedata
import json
import subprocess
import threading
import sqlite3
from datetime import datetime, timezone

# Ensure the root STOCK directory is in sys.path so 'scripts' can be imported dynamically
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

app = Flask(__name__)

def get_secret(key):
    val = os.environ.get(key)
    if val: return val
    try:
        with open('/home/ubuntu/.stock_secrets', 'r') as f:
            for line in f:
                if "=" in line:
                    k, v = line.strip().split('=', 1)
                    v = v.strip('"\'')
                    if key == 'LINE_CHANNEL_ACCESS_TOKEN' and k == 'LINE_TOKEN': return v
                    if key == 'LINE_CHANNEL_SECRET' and k == 'LINE_CHANNEL_SECRET': return v
                    if k == key: return v
    except Exception:
        pass
    try:
        import streamlit as st
        return st.secrets.get(key, '')
    except Exception:
        return ''

line_bot_api = LineBotApi(get_secret('LINE_CHANNEL_ACCESS_TOKEN'))
line_handler = WebhookHandler(get_secret('LINE_CHANNEL_SECRET'))

def reply_line(reply_token, messages, attempts=3):
    global line_bot_api
    if not isinstance(messages, list):
        messages = [messages]
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            line_bot_api.reply_message(reply_token, messages)
            if attempt > 1:
                print(f"LINE reply succeeded attempt {attempt}/{attempts}", flush=True)
            return True
        except Exception as exc:
            last_error = exc
            print(
                f"LINE reply failed attempt {attempt}/{attempts}: "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
            if attempt < attempts:
                line_bot_api = LineBotApi(get_secret('LINE_CHANNEL_ACCESS_TOKEN'))
                time.sleep(0.4 * attempt)
    print(f"LINE reply permanently failed: {type(last_error).__name__}: {last_error}", flush=True)
    return False

ETF_QUOTE_NAMES = {
    "00403A": "主動統一升級50",
    "00981A": "主動統一台股增長",
    "00988A": "主動統一全球創新",
    "00991A": "主動復華未來50",
    "0050": "元大台灣50",
    "0056": "元大高股息",
    "00830": "國泰費城半導體",
    "00878": "國泰永續高股息",
    "00891": "中信關鍵半導體",
    "00918": "大華優利高填息30",
    "009805": "新光美國電力基建",
    "009820": "元大納斯達克精選",
}

# The rich menu sends these exact tokens; quote commands are exact-match only
# (no aliases) so e.g. "抓取891" never leaks into the 891 quote card.
ETF_QUOTE_ALIASES = {
    "403": "00403A",
    "981": "00981A",
    "988": "00988A",
    "991": "00991A",
    "0050": "0050",
    "56": "0056",
    "0056": "0056",
    "830": "00830",
    "878": "00878",
    "891": "00891",
    "918": "00918",
    "9805": "009805",
    "9820": "009820",
}

def parse_etf_quote_command(text):
    return ETF_QUOTE_ALIASES.get(unicodedata.normalize("NFKC", text).strip())

def is_master_holding_command(text):
    normalized = unicodedata.normalize("NFKC", text).strip()
    return "吳大師" in normalized

def is_tag_flow_insight_command(text):
    normalized = unicodedata.normalize("NFKC", text).strip()
    return normalized in {"題材洞察", "類股洞察", "ETF題材洞察"}

def tag_flow_insight_quick_reply():
    return QuickReply(
        items=[
            QuickReplyButton(
                action=MessageAction(label="🔥 今日類股洞察", text="題材洞察")
            )
        ]
    )

def load_tag_flow_insight_payload():
    path = os.path.join(parent_dir, "data", "tag_flow_insight.json")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)

def load_tag_flow_insight_text():
    payload = load_tag_flow_insight_payload()
    text = str(payload.get("line_text") or "").strip()
    if not text:
        raise RuntimeError("tag_flow_insight.json has no line_text")
    return text

def is_daily_update_command(text):
    # Admin command: exact match only (no aliases/fuzzy matching).
    return unicodedata.normalize("NFKC", text).strip() == "每日更新"

def is_gold_command(text):
    normalized = unicodedata.normalize("NFKC", text).strip().lower()
    compact = re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", normalized)
    return "黃金" in normalized or "黄金" in normalized or compact in {"gold", "xau", "xauusd"}

def is_market_pulse_command(text):
    normalized = unicodedata.normalize("NFKC", text).strip().lower()
    compact = re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", normalized)
    return (
        "市場脈動" in normalized
        or "市场脉动" in normalized
        or compact in {"marketpulse", "pulse", "markethealth"}
    )

def latest_market_pulse_date():
    db_path = os.path.join(parent_dir, "data", "etf_bench", "etf_bench.sqlite")
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT MAX(date) FROM prices WHERE ticker = '^TWII'").fetchone()
    if not row or not row[0]:
        raise RuntimeError("No ^TWII price date found in etf_bench DB")
    return str(row[0])

def _run_daily_update():
    """Fire-and-forget re-run of the full daily orchestrator. The script is
    self-contained (cd's to repo, activates venv, sources secrets, defaults to
    all ETFs, and emails its own summary), so there is no LINE feedback here."""
    try:
        subprocess.run(
            ["bash", "scripts/update_and_notify.sh"],
            cwd=parent_dir,
            timeout=1800,
        )
    except Exception as e:
        print("Daily update run failed:", e)

ACTIVE_ETF_TICKERS = {"00403A", "00981A", "00988A", "00991A"}

def parse_refetch_command(text):
    """Admin command: exact '抓取 <token>' only (e.g. '抓取 891', '抓取 全部').
    Tokens are the same exact strings as the quote commands, plus 全部 for all.
    Returns a ticker, 'ALL', or None — no fuzzy matching."""
    normalized = unicodedata.normalize("NFKC", text).strip()
    parts = normalized.split()
    if len(parts) != 2 or parts[0] != "抓取":
        return None
    if parts[1] == "全部":
        return "ALL"
    return ETF_QUOTE_ALIASES.get(parts[1])

def _fetcher_script_for(ticker):
    if ticker in ACTIVE_ETF_TICKERS:
        return f"scripts/fetch_etf_{ticker}.py"
    return f"scripts/fetch_passive_{ticker}.py"

def _read_fetch_log(ticker):
    name = (
        f"etf_{ticker}_log.json" if ticker in ACTIVE_ETF_TICKERS
        else f"passive_{ticker}_log.json"
    )
    try:
        with open(os.path.join(parent_dir, "data", name), encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}

def _run_fetch_and_report(tickers):
    lines = []
    for ticker in tickers:
        script = _fetcher_script_for(ticker)
        try:
            proc = subprocess.run(
                [sys.executable, script],
                cwd=parent_dir,
                capture_output=True,
                text=True,
                timeout=180,
            )
            if proc.returncode == 0:
                log = _read_fetch_log(ticker)
                status = log.get("status", "UNKNOWN")
                latest = log.get("latest_date", "----")
                count = log.get("holdings_count", "?")
                lines.append(
                    f"✅ {ticker} {ETF_QUOTE_NAMES.get(ticker, '')}｜{status}｜{latest}｜{count}檔"
                )
            else:
                tail = (proc.stderr or proc.stdout or "").strip().splitlines()
                detail = tail[-1][:120] if tail else f"exit={proc.returncode}"
                lines.append(f"❌ {ticker} 失敗：{detail}")
        except subprocess.TimeoutExpired:
            lines.append(f"❌ {ticker} 逾時（>180秒）")
        except Exception as e:
            lines.append(f"❌ {ticker} 失敗：{type(e).__name__}: {e}")
    msg = "📥 重新抓取結果\n" + "\n".join(lines)
    print(msg, flush=True)

def _line_access_token():
    return get_secret('LINE_CHANNEL_ACCESS_TOKEN') or get_secret('LINE_TOKEN')

def _parse_iso_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None

def _ago_zh(value):
    dt = _parse_iso_time(value)
    if not dt:
        return "----"
    seconds = max(0, int((datetime.now(timezone.utc) - dt).total_seconds()))
    if seconds < 60:
        return f"{seconds}秒前"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}分鐘前"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}小時前"
    return f"{hours // 24}天前"

def build_etf_quote_text(ticker):
    cache_path = os.path.join(parent_dir, "data", "quote_cache", f"etf_{ticker}_quotes.json")
    with open(cache_path, "r", encoding="utf-8") as fh:
        cache = json.load(fh)
    holdings = cache.get("holdings") or []
    if cache.get("status") == "error":
        raise RuntimeError(f"Quote cache error for {ticker}: {cache.get('error', 'unknown error')}")
    if not holdings:
        raise RuntimeError(f"Quote cache has no holdings for {ticker}")
    counts = cache.get("counts", {})
    composite = cache.get("composite_move_pct")
    etf_name = ETF_QUOTE_NAMES.get(ticker, "")
    fallback_rows = [row for row in holdings if row.get("day_change_pct") is not None and row.get("weight_pct") is not None]
    country_labels = {"TW": "台", "US": "美", "JP": "日", "HK": "港", "TSMC_FUT": "期"}
    country_order = ["TW", "US", "JP", "HK", "TSMC_FUT"]
    fallback_countries = {str(row.get("country") or "").upper() for row in fallback_rows if row.get("country")}
    fallback_scope = "".join(
        country_labels.get(country, country)
        for country in country_order
        if country in fallback_countries
    )
    fallback_scope += "".join(
        country_labels.get(country, country)
        for country in sorted(fallback_countries - set(country_order))
    )
    composite_scope = str(cache.get("composite_country_scope") or fallback_scope or "--").replace("台積電期貨", "期")
    composite_count = cache.get("composite_holding_count")
    if composite_count is None:
        composite_count = len(fallback_rows)
    composite_weight = cache.get("composite_weight_pct")
    if composite_weight is None and fallback_rows:
        composite_weight = sum(float(row.get("weight_pct") or 0) for row in fallback_rows)
    composite_weight_text = "--" if composite_weight is None else f"{float(composite_weight):.1f}%"

    def pct_icon(value):
        if value is None:
            return "⚪"
        value = float(value)
        if value > 0:
            return "🔴"
        if value < 0:
            return "🟢"
        return "⚪"

    def composite_lines_for(label, pct_key, scope_key, count_key, weight_key):
        pct = cache.get(pct_key)
        if pct is None:
            return None
        scope = str(cache.get(scope_key) or "--").replace("台積電期貨", "期")
        count = cache.get(count_key)
        weight = cache.get(weight_key)
        weight_text = "--" if weight is None else f"{float(weight):.1f}%"
        detail_parts = []
        if count is not None:
            detail_parts.append(f"{count}檔")
        if weight is not None:
            detail_parts.append(f"權重{weight_text}")
        scope_text = scope if scope and scope != "--" else "--"
        lines = [f"{label}（{scope_text}）：", f"{pct_icon(pct)} {float(pct):.2f}%"]
        if detail_parts:
            lines.append(f"（{'・'.join(detail_parts)}）")
        return lines
    lines = [
        f"📊{ticker} {etf_name}",
        "──────────",
        f"持股日期：{cache.get('holdings_date', '----')}",
    ]
    composite_blocks = [
        composite_lines_for("交易中漲跌", "composite_live_move_pct", "composite_live_scope", "composite_live_count", "composite_live_weight_pct"),
        composite_lines_for("已收盤漲跌", "composite_notlive_move_pct", "composite_notlive_scope", "composite_notlive_count", "composite_notlive_weight_pct"),
    ]
    composite_blocks = [block for block in composite_blocks if block]
    if composite_blocks:
        for block in composite_blocks:
            lines.append("")
            lines.extend(block)
    elif cache.get("composite_mode") == "live" and composite is not None:
        comp_text = f"{composite:.2f}%"
        composite_label = f"交易中漲跌（{composite_scope}）："
        lines.append("")
        lines.append(composite_label)
        lines.append(f"{pct_icon(composite)} {comp_text}")
        lines.append(f"（{composite_count}檔・權重{composite_weight_text}）")
    lines.extend([
        "",
        f"漲跌統計：🔴{counts.get('up', 0)}　🟢{counts.get('down', 0)}　⚪{counts.get('flat', 0)}",
    ])
    return "\n".join(lines)

def _fetch_intraday_change_pct(symbol, hours=24):
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(
            f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=5d&interval=1h',
            headers=headers,
            timeout=5,
        )
        r.raise_for_status()
        result = r.json()['chart']['result'][0]
        meta = result['meta']
        price = float(meta['regularMarketPrice'])
        timestamps = result.get('timestamp') or []
        closes = result['indicators']['quote'][0].get('close') or []
        valid_data = [(int(ts), float(c)) for ts, c in zip(timestamps, closes) if ts is not None and c is not None]
        if not valid_data:
            return None

        target = int(meta.get('regularMarketTime') or valid_data[-1][0]) - hours * 3600
        old_ts, old_price = min(valid_data, key=lambda item: abs(item[0] - target))
        if old_price <= 0:
            return None
        return (price - old_price) / old_price * 100.0
    except Exception:
        return None

def _fetch_tradingview_quotes(scanner, tickers):
    try:
        payload = {
            "symbols": {"tickers": tickers, "query": {"types": []}},
            "columns": [
                "name",
                "close",
                "change",
                "change_abs",
                "description",
                "currency",
                "update_mode",
            ],
        }
        r = requests.post(
            f"https://scanner.tradingview.com/{scanner}/scan",
            json=payload,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=5,
        )
        r.raise_for_status()
        out = {}
        for row in r.json().get("data") or []:
            values = row.get("d") or []
            if len(values) < 3 or values[1] is None:
                continue
            out[row.get("s")] = {
                "name": values[0] if len(values) > 0 else row.get("s"),
                "price": float(values[1]),
                "change_pct": float(values[2]) if values[2] is not None else None,
                "change_abs": float(values[3]) if len(values) > 3 and values[3] is not None else None,
                "description": values[4] if len(values) > 4 else None,
                "currency": values[5] if len(values) > 5 else "",
                "update_mode": values[6] if len(values) > 6 else None,
            }
        return out
    except Exception as exc:
        print("TradingView quote fetch failed:", exc)
        return {}

def get_yahoo_data_text(symbol, title, emoji, precision=2, intraday_1d=False):
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=6mo&interval=1d', headers=headers, timeout=5)
        if r.status_code == 200:
            data = r.json()
            result = data['chart']['result'][0]
            meta = result['meta']
            
            price = meta['regularMarketPrice']
            currency = meta.get('currency', '')
            
            timestamps = result['timestamp']
            closes = result['indicators']['quote'][0]['close']
            
            # Filter valid data
            valid_data = [(ts, c) for ts, c in zip(timestamps, closes) if c is not None]
            
            if not valid_data:
                return f"{emoji} {title}\n──────────\n無有效報價資料。"
                
            def format_change_pct(change_pct, label):
                sign = "+" if change_pct > 0 else ""
                direction_emoji = "🔴" if change_pct > 0 else "🟢"
                if change_pct == 0:
                    direction_emoji = "⚪"
                return f"{label} {direction_emoji}{sign}{change_pct:.2f}%"

            def get_change_str(days_ago, label):
                if len(valid_data) <= days_ago:
                    return f" {label} 無資料"
                    
                old_price = valid_data[-(days_ago + 1)][1]
                change = price - old_price
                change_pct = (change / old_price) * 100
                return format_change_pct(change_pct, label)

            one_day_text = get_change_str(1, "1日：")
            if intraday_1d:
                intraday_change = _fetch_intraday_change_pct(symbol, hours=24)
                if intraday_change is not None:
                    one_day_text = format_change_pct(intraday_change, "1日：")

            currency_zh = {"USD": "美元", "TWD": "台幣", "CHF": "瑞郎", "JPY": "日圓",
                           "GBP": "英鎊", "EUR": "歐元", "HKD": "港幣"}.get(currency, currency)
            price_str = f"{price:.{precision}f}"
            lines = [
                f"{emoji} {title}",
                f"──────────",
                f"🕒 最新報價：{price_str} {currency_zh}",
                f"",
                f"📊 近期漲跌幅：",
                one_day_text,
                get_change_str(5,  "1週："),
                get_change_str(21, "1月："),
                get_change_str(len(valid_data)-1, "6月："),
            ]
            
            return "\n".join(lines)
        else:
            return f"{emoji} {title}\n──────────\n報價暫時無法使用。"
    except Exception as e:
        return f"{emoji} {title}\n──────────\n無法取得目前報價資訊，請稍後再試。"

def get_fx_data_text(yahoo_symbol, tradingview_symbol, title, emoji, precision=3):
    tv_quote = _fetch_tradingview_quotes("forex", [tradingview_symbol]).get(tradingview_symbol)
    if not tv_quote:
        return get_yahoo_data_text(yahoo_symbol, title, emoji, precision=precision, intraday_1d=True)

    try:
        price = float(tv_quote["price"])
        one_day_change = tv_quote.get("change_pct")

        headers = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(
            f'https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol}?range=6mo&interval=1d',
            headers=headers,
            timeout=5,
        )
        r.raise_for_status()
        result = r.json()['chart']['result'][0]
        timestamps = result['timestamp']
        closes = result['indicators']['quote'][0]['close']
        valid_data = [(ts, c) for ts, c in zip(timestamps, closes) if c is not None]
        if not valid_data:
            raise ValueError("no yahoo history")

        def format_change_pct(change_pct, label):
            sign = "+" if change_pct > 0 else ""
            direction_emoji = "🔴" if change_pct > 0 else "🟢"
            if change_pct == 0:
                direction_emoji = "⚪"
            return f"{label} {direction_emoji}{sign}{change_pct:.2f}%"

        def get_change_str(days_ago, label):
            if len(valid_data) <= days_ago:
                return f" {label} 無資料"
            old_price = valid_data[-(days_ago + 1)][1]
            change_pct = (price - old_price) / old_price * 100
            return format_change_pct(change_pct, label)

        currency_zh = {"TWD": "台幣", "CHF": "瑞郎", "JPY": "日圓"}.get(tv_quote.get("currency"), tv_quote.get("currency", ""))
        price_str = f"{price:.{precision}f}"
        one_day_text = "1日： 無資料" if one_day_change is None else format_change_pct(float(one_day_change), "1日：")
        return "\n".join([
            f"{emoji} {title}",
            "──────────",
            f"🕒 最新報價：{price_str} {currency_zh}",
            "",
            "📊 近期漲跌幅：",
            one_day_text,
            get_change_str(5, "1週："),
            get_change_str(21, "1月："),
            get_change_str(len(valid_data) - 1, "6月："),
        ])
    except Exception as exc:
        print("FX history text failed:", exc)
        return get_yahoo_data_text(yahoo_symbol, title, emoji, precision=precision, intraday_1d=True)

def get_yahoo_data_dict(symbol, precision=2):
    """Helper to get raw data for the screenshot overlay."""
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=5d&interval=1d', headers=headers, timeout=5)
        if r.status_code == 200:
            data = r.json()
            result = data['chart']['result'][0]
            meta = result['meta']
            price = meta['regularMarketPrice']
            
            timestamps = result['timestamp']
            closes = result['indicators']['quote'][0]['close']
            valid_data = [(ts, c) for ts, c in zip(timestamps, closes) if c is not None]
            
            old_price = valid_data[-2][1] if len(valid_data) >= 2 else price
            change = price - old_price
            change_pct = (change / old_price) * 100
            
            sign = "+" if change >= 0 else ""
            return {"price": f"{price:.{precision}f}", "change": f"{sign}{change_pct:.2f}%", "raw_change": change}
    except:
        pass
    return {"price": "0.00", "change": "0.00%", "raw_change": 0}

def get_oil_price():
    parts = []
    parts.append(get_yahoo_data_text('CL=F', '輕原油', '🛢️', precision=2))
    parts.append(get_yahoo_data_text('BZ=F', '布蘭特原油', '🛢️', precision=2))
    return "\n\n".join(parts)

def get_10yf_price():
    return get_yahoo_data_text('^TNX', '美國10年期公債殖利率', '📈', precision=3)

def get_exchange_rates():
    parts = []
    parts.append(get_fx_data_text('TWD=X', 'FX_IDC:USDTWD', '美元兌台幣', '💵', precision=3))
    parts.append(get_fx_data_text('CHF=X', 'OANDA:USDCHF', '美元兌瑞朗', '💷', precision=4))
    parts.append(get_fx_data_text('JPY=X', 'OANDA:USDJPY', '美元兌日幣', '💴', precision=2))
    return "\n\n".join(parts)

def get_gold_text():
    try:
        cache_path = os.path.join(parent_dir, "data", "quote_cache", "gold_quote.json")
        if not os.path.exists(cache_path):
            from scripts.monitor_gold_quote import refresh_once
            quote = refresh_once()
        else:
            with open(cache_path, "r", encoding="utf-8") as fh:
                quote = json.load(fh)

        price = float(quote["price"])
        change = quote.get("change_pct")
        change_text = "----" if change is None else f"{float(change):+.2f}%"
        updated = _ago_zh(quote.get("quote_time_utc"))
        performance = quote.get("performance") or {}
        lines = [
            "黃金 GOLD",
            "──────────",
            f"最新報價：{price:,.2f} {quote.get('currency', 'USD')}",
            f"今日漲跌：{change_text}",
            f"更新：{updated}",
            "來源：TradingView",
        ]
        if performance:
            labels = [
                ("1d", "1日"),
                ("5d", "5日"),
                ("1m", "1月"),
                ("6m", "6月"),
                ("ytd", "今年"),
                ("1y", "1年"),
            ]
            lines.append("")
            lines.append("期間績效：")
            for key, label in labels:
                if key in performance:
                    lines.append(f"{label}：{float(performance[key]):+.2f}%")
        return "\n".join(lines)
    except Exception as exc:
        print("Gold quote failed:", exc)
        return "黃金報價暫時無法取得，請稍後再試。"

CHART_SERVICE_URL = os.environ.get("CHART_SERVICE_URL", "http://127.0.0.1:5005")
MARKET_TEXT_ERROR_LABELS = {
    "oil": "WTI 輕原油",
    "brent": "布蘭特原油",
    "bond": "美國10年期公債殖利率",
    "gold": "黃金 GOLD",
    "usdtwd": "美元兌台幣",
    "usdchf": "美元兌瑞郎",
    "usdjpy": "美元兌日幣",
    "nasdaq": "那斯達克 NASDAQ",
}

def _exception_detail(exc):
    response = getattr(exc, "response", None)
    if response is not None:
        body = (getattr(response, "text", "") or "").strip()
        if len(body) > 600:
            body = body[:600] + "..."
        return f"{type(exc).__name__}: HTTP {response.status_code} {body}".strip()
    return f"{type(exc).__name__}: {exc}"

def _tradingview_error_text(key, stage, exc):
    label = MARKET_TEXT_ERROR_LABELS.get(key, key)
    return f"{label}\n──────────\nTradingView {stage}錯誤：\n{_exception_detail(exc)}"

def get_market_text(key):
    try:
        response = requests.post(
            f"{CHART_SERVICE_URL}/market-text",
            json={"key": key},
            timeout=20,
        )
        response.raise_for_status()
        return response.json()["text"]
    except Exception as exc:
        print(f"Chart market text failed for {key}: {exc}")
        return _tradingview_error_text(key, "文字報價", exc)

def get_chart_snapshot(key, timeout=30):
    response = requests.post(
        f"{CHART_SERVICE_URL}/snapshot",
        json={"key": key},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("url"):
        raise RuntimeError(f"Chart service returned no snapshot URL for {key}: {payload}")
    return payload

def get_cached_market_chart(key, max_age_seconds=300):
    cache_path = os.path.join(parent_dir, "data", "quote_cache", f"market_{key}.json")
    try:
        with open(cache_path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except FileNotFoundError:
        raise RuntimeError(f"Missing cached TradingView market chart: {cache_path}")

    updated_at = payload.get("updated_at")
    if not updated_at:
        raise RuntimeError(f"Cached TradingView market chart has no updated_at: {cache_path}")
    updated_dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    age = (datetime.now(timezone.utc) - updated_dt).total_seconds()
    if age > max_age_seconds:
        raise RuntimeError(f"Cached TradingView market chart is stale: {key} age={age:.0f}s")

    text = payload.get("text")
    image_url = payload.get("snapshot_url")
    if not text:
        raise RuntimeError(f"Cached TradingView market chart has no text: {cache_path}")
    if not image_url:
        raise RuntimeError(f"Cached TradingView market chart has no snapshot_url: {cache_path}")
    image_path = os.path.join(parent_dir, "data", "images", image_url)
    if not os.path.exists(image_path):
        raise RuntimeError(f"Cached TradingView market image is missing: {image_path}")

    return payload

# All market commands are served from the 1-minute cache written by
# stock-market-chart-monitor.service. The webhook never renders TradingView
# live anymore — it only reads cache so replies are fast and the text price
# always matches the cached chart (same-moment capture in chart_service).
# max_age 240s tolerates a couple of failed 60s refreshes before going stale.
MARKET_CACHE_MAX_AGE = 240

def _cached_market_text(key):
    try:
        return get_cached_market_chart(key, max_age_seconds=MARKET_CACHE_MAX_AGE)["text"]
    except Exception as exc:
        print(f"Cached market text failed for {key}: {exc}")
        return _tradingview_error_text(key, "快取報價", exc)

def reply_cached_market(reply_token, keys):
    """Reply a market card (text + chart) entirely from cache.

    One combined text block for all keys, followed by each key's cached chart
    image. A missing/stale key degrades to an inline error line so the rest of
    the card still sends.
    """
    texts, images = [], []
    for key in keys:
        try:
            cache = get_cached_market_chart(key, max_age_seconds=MARKET_CACHE_MAX_AGE)
            texts.append(cache["text"])
            img_url = f"https://linechatbot.duckdns.org/api/webhook/images/{cache['snapshot_url']}?t={int(time.time())}"
            images.append(ImageSendMessage(original_content_url=img_url, preview_image_url=img_url))
        except Exception as exc:
            print(f"Cached market reply failed for {key}: {exc}")
            texts.append(_tradingview_error_text(key, "快取圖表", exc))
    messages = [TextSendMessage(text="\n\n".join(texts))] + images
    reply_line(reply_token, messages)

def get_oil_price():
    return "\n\n".join(_cached_market_text(key) for key in ["oil", "brent"])

def get_10yf_price():
    return _cached_market_text("bond")

def get_exchange_rates():
    return "\n\n".join(_cached_market_text(key) for key in ["usdtwd", "usdchf", "usdjpy"])

def get_gold_text():
    return _cached_market_text("gold")

@app.route('/', methods=['GET'])
@app.route('/api/webhook', methods=['GET'])
def home():
    return "LINE Bot is running securely!", 200

@app.route('/api/webhook/images/<filename>', methods=['GET'])
def serve_image(filename):
    return send_from_directory('/home/ubuntu/STOCK/data/images', filename)

@app.route('/api/webhook/summaries/<filename>', methods=['GET'])
def serve_summary(filename):
    return send_from_directory('/home/ubuntu/STOCK/data/summaries', filename)

@app.route('/api/webhook', methods=['POST'])
def webhook():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    
    try:
        line_handler.handle(body, signature)
    except InvalidSignatureError:
        return "Invalid signature", 400
    return "OK", 200

@line_handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_msg = event.message.text.strip()
    is_master_holding = is_master_holding_command(user_msg)
    is_tag_flow_insight = is_tag_flow_insight_command(user_msg)
    is_daily_update = is_daily_update_command(user_msg)
    is_gold = is_gold_command(user_msg)
    is_market_pulse = is_market_pulse_command(user_msg)
    refetch_target = parse_refetch_command(user_msg)
    etf_quote_ticker = (
        None
        if is_daily_update
        or is_master_holding
        or is_tag_flow_insight
        or is_gold
        or is_market_pulse
        or refetch_target
        else parse_etf_quote_command(user_msg)
    )
    print(f"LINE text={user_msg!r} parsed_etf={etf_quote_ticker} refetch={refetch_target} daily_update={is_daily_update}", flush=True)
    if is_master_holding:
        try:
            from scripts.master_holding_quote_card import load_cached_master_quote_card

            text, output_paths, cache = load_cached_master_quote_card()
            messages = [
                TextSendMessage(
                    text=text,
                    quick_reply=tag_flow_insight_quick_reply(),
                )
            ]
            for output_path in output_paths[:2]:
                img_url = f"https://linechatbot.duckdns.org/api/webhook/images/{os.path.basename(output_path)}?t={int(time.time())}"
                messages.append(ImageSendMessage(original_content_url=img_url, preview_image_url=img_url))
            reply_line(event.reply_token, messages)
        except Exception as e:
            print("Master holding card generation failed:", e)
            reply_line(
                event.reply_token,
                TextSendMessage(text="吳大師持股暫時無法產生，請稍後再試。")
            )

    elif is_tag_flow_insight:
        try:
            payload = load_tag_flow_insight_payload()
            filename = os.path.basename(
                str(payload.get("image") or "tag_flow_insight_latest.jpg")
            )
            if not filename.lower().endswith((".jpg", ".jpeg", ".png")):
                raise RuntimeError(f"Unsupported insight image: {filename}")
            image_path = os.path.join(parent_dir, "data", "summaries", filename)
            if not os.path.exists(image_path):
                raise FileNotFoundError(f"Missing cached insight image: {image_path}")
            img_url = f"https://linechatbot.duckdns.org/api/webhook/summaries/{filename}?t={int(os.path.getmtime(image_path))}"
            reply_line(
                event.reply_token,
                ImageSendMessage(original_content_url=img_url, preview_image_url=img_url),
            )
        except Exception as e:
            print("Theme insight image reply failed:", e)
            reply_line(
                event.reply_token,
                TextSendMessage(text="今日類股洞察尚未更新完成，請稍後再試。"),
            )

    elif is_market_pulse:
        try:
            filename = "market_pulse_latest.jpg"
            image_path = os.path.join(parent_dir, "data", "summaries", filename)
            if not os.path.exists(image_path):
                raise FileNotFoundError(f"Missing cached market pulse image: {image_path}")

            latest_date = latest_market_pulse_date()
            img_url = f"https://linechatbot.duckdns.org/api/webhook/summaries/{filename}?t={int(time.time())}"
            reply_line(
                event.reply_token,
                [
                    TextSendMessage(text=f"市場脈動｜資料截至 {latest_date}"),
                    ImageSendMessage(original_content_url=img_url, preview_image_url=img_url),
                ],
            )
        except Exception as e:
            print("Market pulse cached image reply failed:", e)
            reply_line(
                event.reply_token,
                TextSendMessage(text="市場脈動截圖尚未更新完成，請稍後再試。")
            )

    elif is_gold:
        reply_cached_market(event.reply_token, ["gold"])

    elif etf_quote_ticker:
        try:
            from scripts.generate_quote_card import cached_quote_card_paths

            text = build_etf_quote_text(etf_quote_ticker)
            output_paths = cached_quote_card_paths(etf_quote_ticker, max_pages=2)
            if not output_paths:
                raise FileNotFoundError(f"Missing cached quote card images for {etf_quote_ticker}")
            messages = [TextSendMessage(text=text)]
            for output_path in output_paths[:2]:
                img_url = f"https://linechatbot.duckdns.org/api/webhook/images/{os.path.basename(output_path)}?t={int(time.time())}"
                messages.append(ImageSendMessage(original_content_url=img_url, preview_image_url=img_url))
            reply_line(event.reply_token, messages)
        except Exception as e:
            print("ETF quote cache reply failed:", e)
            reply_line(
                event.reply_token,
                TextSendMessage(text=f"{etf_quote_ticker} 報價圖暫時無法產生：{type(e).__name__}: {e}")
            )

    elif is_daily_update:
        # Re-run the full daily orchestrator (fetch + benchmark + git + LINE
        # broadcast + admin email). Fire-and-forget: the email is the report,
        # so the bot only acks that it started (a free reply, not a paid push).
        reply_line(
            event.reply_token,
            TextSendMessage(text="⏳ 已開始重新執行每日更新，結果將寄至 email。")
        )
        threading.Thread(target=_run_daily_update, daemon=True).start()

    elif refetch_target:
        if refetch_target == "ALL":
            tickers = list(ETF_QUOTE_NAMES.keys())
        else:
            tickers = [refetch_target]
        reply_line(
            event.reply_token,
            TextSendMessage(text=f"⏳ 開始重新抓取：{'、'.join(tickers)}\n結果會寫入 stock_webhook.log（每檔約需數十秒）。")
        )
        threading.Thread(
            target=_run_fetch_and_report,
            args=(tickers,),
            daemon=True,
        ).start()

    elif user_msg in {"那斯達克", "那指", "納斯達克", "24小時那斯達克"} or user_msg.strip().lower() in {"nasdaq", "ndx", "nas"}:
        reply_cached_market(event.reply_token, ["nasdaq"])

    elif user_msg == "油價":
        reply_cached_market(event.reply_token, ["oil", "brent"])

    elif user_msg in ["債卷", "債券"]:
        reply_cached_market(event.reply_token, ["bond"])

    elif user_msg == "匯率":
        reply_cached_market(event.reply_token, ["usdtwd", "usdjpy", "usdchf"])
    elif user_msg.lower() == "admin":
        reply_line(
            event.reply_token,
            TextSendMessage(text=(
                "🔑 隱藏指令清單\n"
                "━━━━━━━━━━━━━━\n\n"
                "📊 一般隱藏指令\n"
                "• id — 查詢 LINE 使用者 ID 及群組 ID\n\n"
                "🔁 每日更新（背景執行，結果寄 email）\n"
                "• 每日更新 — 重新執行完整每日流程（抓取＋benchmark＋git＋廣播＋email）\n\n"
                "🔄 重新抓取官方持股（完成後回報狀態）\n"
                "• 抓取 891 — 重新抓取單一 ETF（403/981/988/0050/830/878/891/918/9805/9820）\n"
                "• 抓取 全部 — 重新抓取所有 ETF\n"
                "🥚 彩蛋\n"
                "• 欸嘿 — ( ͡° ͜ʖ ͡°)\n\n"
                "ℹ️ 以上指令均需手動輸入，不在選單中顯示。"
            ))
        )

    elif user_msg.lower() == "id":
        reply_parts = [f"使用者 ID：{event.source.user_id}"]
        if event.source.type == "group":
            reply_parts.append(f"群組 ID：{event.source.group_id}")
        elif event.source.type == "room":
            reply_parts.append(f"聊天室 ID：{event.source.room_id}")
            
        reply_line(
            event.reply_token,
            TextSendMessage(text="\n".join(reply_parts))
        )
    elif user_msg == "欸嘿":
        reply_line(
            event.reply_token,
            TextSendMessage(text="欸嘿")
        )
    else:
        reply_line(
            event.reply_token,
            TextSendMessage(text=(
            "可用關鍵字：\n"
            "• 油價 — 西德州輕原油與布蘭特原油報價\n"
            "• 匯率 — 美元兌台幣、瑞郎、日圓\n"
            "• 債券 — 美國10年期公債殖利率\n"
            "• 黃金 — TradingView GOLD 報價與圖\n"
            "• 那斯達克 — NASDAQ 24小時即時指標與圖\n"
            "• 市場脈動 — 加權指數市場狀態截圖\n"
            "• 403 — 00403A 持股即時表\n"
            "• 981 — 00981A 持股即時表\n"
            "• 988 — 00988A 持股即時表\n"
            "• 991 — 00991A 持股即時表\n"
            "• 0050 — 元大台灣50 持股即時表\n"
            "• 56 — 0056 元大高股息 持股即時表\n"
            "• 830 — 00830 持股即時表\n"
            "• 878 — 00878 持股即時表\n"
            "• 891 — 00891 持股即時表\n"
            "• 918 — 00918 持股即時表\n"
            "• 9805 — 009805 持股即時表\n"
            "• 9820 — 009820 持股即時表\n"
            "• 吳大師 — 投資組合與展開持股\n"
            "• 題材洞察 — 強勢加速類股與三檔 ETF 共買池\n"
            "• id — 取得使用者或群組 ID"
        ))
        )

@line_handler.add(FollowEvent)
def handle_follow(event):
    reply_line(
        event.reply_token,
        TextSendMessage(text="歡迎加入！🤖\n請點選下方選單查詢報價與財經資訊，或直接在對話框輸入關鍵字。\n輸入「id」可取得您的 LINE 使用者 ID 與群組 ID。")
    )

@line_handler.add(PostbackEvent)
def handle_postback(event):
    # Rich menu page-switch events — no action needed, switch happens client-side
    pass

@line_handler.add(JoinEvent)
def handle_join(event):
    reply_line(
        event.reply_token,
        TextSendMessage(text="大家好！🤖\n我已加入這個群組。請點選下方選單查詢報價與財經資訊，或直接輸入關鍵字。\n輸入「id」可取得目前的使用者 ID 與群組 ID。")
    )

# Local deployment entrypoint
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
