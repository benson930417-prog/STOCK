from flask import Flask, request, send_from_directory
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FollowEvent, JoinEvent, ImageSendMessage, PostbackEvent
import time
import os
import sys
import requests
import re
import unicodedata
import json
import subprocess
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

ETF_QUOTE_NAMES = {
    "00981A": "主動統一台股增長",
    "00997A": "主動群益美國增長",
    "0050": "元大台灣50",
    "00830": "國泰費城半導體",
    "00878": "國泰永續高股息",
    "009805": "新光美國電力基建",
    "009820": "元大納斯達克精選",
}

def parse_etf_quote_command(text):
    compact = unicodedata.normalize("NFKC", text).lower()
    compact = re.sub(r"[^0-9a-z]", "", compact)
    if "997" in compact:
        return "00997A"
    if "981" in compact:
        return "00981A"
    if "0050" in compact or compact == "50":
        return "0050"
    if "00830" in compact or compact in {"830", "0830"}:
        return "00830"
    if "00878" in compact or compact in {"878", "0878"}:
        return "00878"
    if "009805" in compact or compact in {"9805", "09805", "9805"}:
        return "009805"
    if "009820" in compact or compact in {"9820", "09820"}:
        return "009820"
    return None

def is_master_holding_command(text):
    normalized = unicodedata.normalize("NFKC", text).strip()
    return "吳大師" in normalized

def is_operation_report_command(text):
    normalized = unicodedata.normalize("NFKC", text).strip()
    return "操作日報" in normalized

def is_gold_command(text):
    normalized = unicodedata.normalize("NFKC", text).strip().lower()
    compact = re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", normalized)
    return "黃金" in normalized or "黄金" in normalized or compact in {"gold", "xau", "xauusd"}

def parse_operation_report_ticker(text):
    compact = unicodedata.normalize("NFKC", text).lower()
    compact = re.sub(r"[^0-9a-z]", "", compact)
    if "997" in compact:
        return "00997A"
    if "981" in compact:
        return "00981A"
    return None

def _line_access_token():
    return get_secret('LINE_CHANNEL_ACCESS_TOKEN') or get_secret('LINE_TOKEN')

def _github_repo():
    return get_secret("GITHUB_REPO") or "benson930417-prog/STOCK"

def _publish_summary_to_github(ticker, image_path):
    rel_image_path = os.path.relpath(image_path, parent_dir).replace(os.sep, "/")
    env = os.environ.copy()
    git_bin = "/usr/bin/git"

    subprocess.run(
        [git_bin, "pull", "origin", "main", "--rebase", "--autostash"],
        cwd=parent_dir,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    subprocess.run(
        [git_bin, "config", "user.name", "Webhook Bot"],
        cwd=parent_dir,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    subprocess.run(
        [git_bin, "config", "user.email", "webhook-bot@localhost"],
        cwd=parent_dir,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    subprocess.run(
        [git_bin, "add", rel_image_path],
        cwd=parent_dir,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )

    has_staged_change = subprocess.run(
        [git_bin, "diff", "--cached", "--quiet", "--", rel_image_path],
        cwd=parent_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    ).returncode != 0

    if has_staged_change:
        subprocess.run(
            [git_bin, "commit", "-m", f"Re-render {ticker} operation report"],
            cwd=parent_dir,
            env=env,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        subprocess.run(
            [git_bin, "push", "origin", "main"],
            cwd=parent_dir,
            env=env,
            check=True,
            capture_output=True,
            text=True,
            timeout=90,
        )
        return True

    return False

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
    counts = cache.get("counts", {})
    composite = cache.get("composite_move_pct")
    etf_name = ETF_QUOTE_NAMES.get(ticker, "")
    holdings = cache.get("holdings", [])
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
    lines = [
        f"{ticker} {etf_name}",
        f"持股日期：{cache.get('holdings_date', '----')}",
    ]
    if cache.get("composite_mode") == "live" and composite is not None:
        comp_text = f"{composite:+.2f}%"
        composite_label = f"即時加權 ({composite_scope})"
        lines.append(f"- {composite_label}：{comp_text}")
        lines.append(f"- 交易中{composite_count}檔（權重{composite_weight_text}）")
    lines.append(f"- 上漲 {counts.get('up', 0)} / 下跌 {counts.get('down', 0)} / 無變動 {counts.get('flat', 0)}")
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
}

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
        label = MARKET_TEXT_ERROR_LABELS.get(key, key)
        return f"{label}\n──────────\nTradingView 文字報價暫時無法取得。"

def get_oil_price():
    return "\n\n".join(get_market_text(key) for key in ["oil", "brent"])

def get_10yf_price():
    return get_market_text("bond")

def get_exchange_rates():
    return "\n\n".join(get_market_text(key) for key in ["usdtwd", "usdchf", "usdjpy"])

def get_gold_text():
    return get_market_text("gold")

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
    is_operation_report = is_operation_report_command(user_msg)
    is_gold = is_gold_command(user_msg)
    etf_quote_ticker = None if is_operation_report or is_master_holding or is_gold else parse_etf_quote_command(user_msg)
    print(f"LINE text={user_msg!r} parsed_etf={etf_quote_ticker}", flush=True)
    
    if is_master_holding:
        try:
            from scripts.master_holding_quote_card import (
                generate_master_quote_card,
                load_cached_master_quote_card,
            )

            try:
                text, output_paths, cache = load_cached_master_quote_card()
            except Exception:
                text, output_paths = generate_master_quote_card(limit=50)
            messages = [TextSendMessage(text=text)]
            image_slots = max(0, 5 - len(messages))
            for output_path in output_paths[:image_slots]:
                img_url = f"https://linechatbot.duckdns.org/api/webhook/images/{os.path.basename(output_path)}?t={int(time.time())}"
                messages.append(ImageSendMessage(original_content_url=img_url, preview_image_url=img_url))
            line_bot_api.reply_message(event.reply_token, messages)
        except Exception as e:
            print("Master holding card generation failed:", e)
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="吳大師持股暫時無法產生，請稍後再試。")
            )

    elif is_gold:
        reply_msg = get_gold_text()
        try:
            snapshot_url = "http://127.0.0.1:5005/snapshot"
            res = requests.post(snapshot_url, json={"key": "gold"}, timeout=30).json()
            img_url = f"https://linechatbot.duckdns.org/api/webhook/images/{res['url']}?t={int(time.time())}"
            line_bot_api.reply_message(
                event.reply_token,
                [
                    TextSendMessage(text=reply_msg),
                    ImageSendMessage(original_content_url=img_url, preview_image_url=img_url),
                ],
            )
        except Exception as e:
            print("Gold Chart generation failed:", e)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_msg))

    elif etf_quote_ticker:
        try:
            from scripts.generate_quote_card import generate_quote_card

            output_paths = generate_quote_card(etf_quote_ticker)
            messages = [TextSendMessage(text=build_etf_quote_text(etf_quote_ticker))]
            for output_path in output_paths[:4]:
                img_url = f"https://linechatbot.duckdns.org/api/webhook/images/{os.path.basename(output_path)}?t={int(time.time())}"
                messages.append(ImageSendMessage(original_content_url=img_url, preview_image_url=img_url))
            line_bot_api.reply_message(event.reply_token, messages)
        except Exception as e:
            print("ETF quote card generation failed:", e)
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"{etf_quote_ticker} 報價圖暫時無法產生，請稍後再試。")
            )

    elif is_operation_report:
        try:
            from scripts.generate_etf_summary import generate

            ticker = parse_operation_report_ticker(user_msg)
            if not ticker:
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="請在操作日報訊息中指定 981 或 997。")
                )
                return

            generate([ticker])
            filename = f"etf_{ticker}_summary_latest.jpg"
            image_path = os.path.join(parent_dir, "data", "summaries", filename)
            if not os.path.exists(image_path):
                raise FileNotFoundError(image_path)
            pushed = _publish_summary_to_github(ticker, image_path)

            history_path = os.path.join(parent_dir, "data", f"etf_{ticker}_history.json")
            with open(history_path, "r", encoding="utf-8") as fh:
                date_str = max(json.load(fh).keys())

            img_url = (
                f"https://raw.githubusercontent.com/{_github_repo()}/main/"
                f"data/summaries/{filename}?t={int(time.time())}"
            )
            messages = [
                {
                    "type": "text",
                    "text": f"{date_str} {ETF_QUOTE_NAMES.get(ticker, ticker)} ({ticker}) 操作日報",
                },
                {
                    "type": "image",
                    "originalContentUrl": img_url,
                    "previewImageUrl": img_url,
                },
            ]
            token = _line_access_token()
            if not token:
                raise RuntimeError("LINE access token is not configured")

            res = requests.post(
                "https://api.line.me/v2/bot/message/broadcast",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                data=json.dumps({"messages": messages}, ensure_ascii=False).encode("utf-8"),
                timeout=20,
            )
            res.raise_for_status()
            status_text = "已重新渲染並廣播。" if pushed else "圖片無變更，已廣播最新版本。"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"{ticker} 操作日報{status_text}"))
        except Exception as e:
            print("ETF operation report broadcast failed:", e)
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="操作日報廣播暫時無法送出，請稍後再試。")
            )

    elif user_msg == "油價":
        reply_msg = get_oil_price()
        try:
            snapshot_url = "http://127.0.0.1:5005/snapshot"
            messages = [TextSendMessage(text=reply_msg)]
            
            # WTI and Brent - chart_service scrapes data from TradingView directly
            for key in ["oil", "brent"]:
                payload = {"key": key}
                res = requests.post(snapshot_url, json=payload, timeout=30).json()
                img_url = f"https://linechatbot.duckdns.org/api/webhook/images/{res['url']}?t={int(time.time())}"
                messages.append(ImageSendMessage(original_content_url=img_url, preview_image_url=img_url))
            
            line_bot_api.reply_message(event.reply_token, messages)
        except Exception as e:
            print("Oil Chart generation failed:", e)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_msg))

    elif user_msg in ["債卷", "債券"]:
        reply_msg = get_10yf_price()
        try:
            snapshot_url = "http://127.0.0.1:5005/snapshot"
            payload = {"key": "bond"}
            res = requests.post(snapshot_url, json=payload, timeout=30).json()
            
            img_url = f"https://linechatbot.duckdns.org/api/webhook/images/{res['url']}?t={int(time.time())}"
            
            line_bot_api.reply_message(
                event.reply_token,
                [
                    TextSendMessage(text=reply_msg),
                    ImageSendMessage(original_content_url=img_url, preview_image_url=img_url)
                ]
            )
        except Exception as e:
            print("Bond Chart generation failed:", e)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_msg))

    elif user_msg == "匯率":
        reply_msg = get_exchange_rates()
        try:
            snapshot_url = "http://127.0.0.1:5005/snapshot"
            messages = [TextSendMessage(text=reply_msg)]
            
            # 3 Quick snapshots - chart_service scrapes data from TradingView directly
            for key in ["usdtwd", "usdjpy", "usdchf"]:
                payload = {"key": key}
                res = requests.post(snapshot_url, json=payload, timeout=30).json()
                img_url = f"https://linechatbot.duckdns.org/api/webhook/images/{res['url']}?t={int(time.time())}"
                messages.append(ImageSendMessage(original_content_url=img_url, preview_image_url=img_url))
            
            line_bot_api.reply_message(event.reply_token, messages)
        except Exception as e:
            print("Forex Chart generation failed:", e)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_msg))
    elif user_msg.lower() == "admin":
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=(
                "🔑 隱藏指令清單\n"
                "━━━━━━━━━━━━━━\n\n"
                "📊 一般隱藏指令\n"
                "• id — 查詢 LINE 使用者 ID 及群組 ID\n\n"
                "📢 管理員廣播（需手動輸入）\n"
                "• 操作日報 981 — 重新渲染並廣播 00981A 操作日報\n"
                "• 操作日報 997 — 重新渲染並廣播 00997A 操作日報\n\n"
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
            
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="\n".join(reply_parts))
        )
    elif user_msg == "欸嘿":
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="欸嘿")
        )
    else:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=(
            "可用關鍵字：\n"
            "• 油價 — 西德州輕原油與布蘭特原油報價\n"
            "• 匯率 — 美元兌台幣、瑞郎、日圓\n"
            "• 債券 — 美國10年期公債殖利率\n"
            "• 黃金 — TradingView GOLD 報價與圖\n"
            "• 981 — 00981A 持股即時表\n"
            "• 997 — 00997A 持股即時表\n"
            "• 0050 — 元大台灣50 持股即時表\n"
            "• 830 — 00830 持股即時表\n"
            "• 878 — 00878 持股即時表\n"
            "• 9805 — 009805 持股即時表\n"
            "• 吳大師 — 投資組合與展開持股\n"
            "• id — 取得使用者或群組 ID"
        ))
        )

@line_handler.add(FollowEvent)
def handle_follow(event):
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text="歡迎加入！🤖\n請點選下方選單查詢報價與財經資訊，或直接在對話框輸入關鍵字。\n輸入「id」可取得您的 LINE 使用者 ID 與群組 ID。")
    )

@line_handler.add(PostbackEvent)
def handle_postback(event):
    # Rich menu page-switch events — no action needed, switch happens client-side
    pass

@line_handler.add(JoinEvent)
def handle_join(event):
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text="大家好！🤖\n我已加入這個群組。請點選下方選單查詢報價與財經資訊，或直接輸入關鍵字。\n輸入「id」可取得目前的使用者 ID 與群組 ID。")
    )

# Local deployment entrypoint
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
