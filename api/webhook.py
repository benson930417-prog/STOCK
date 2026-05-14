from flask import Flask, request, send_from_directory
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FollowEvent, JoinEvent, ImageSendMessage
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
    return None

def is_master_holding_command(text):
    normalized = unicodedata.normalize("NFKC", text).strip()
    return "吳大師" in normalized

def is_operation_report_command(text):
    normalized = unicodedata.normalize("NFKC", text).strip()
    return "操作日報" in normalized

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
    comp_text = "----" if composite is None else f"{composite:+.2f}%"
    etf_name = ETF_QUOTE_NAMES.get(ticker, "")
    return (
        f"{ticker} {etf_name}\n"
        f"持股日期：{cache.get('holdings_date', '----')}\n"
        f"加權漲跌：{comp_text}\n"
        f"上漲 {counts.get('up', 0)} / 下跌 {counts.get('down', 0)} / 無變動 {counts.get('flat', 0)}\n"
        f"最新報價：{_ago_zh(cache.get('newest_quote_utc'))}｜"
        f"最舊報價：{_ago_zh(cache.get('oldest_quote_utc'))}｜"
        f"權重更新：{_ago_zh(cache.get('etf_refresh_utc'))}"
    )

def get_yahoo_data_text(symbol, title, emoji, precision=2):
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
                
            def get_change_str(days_ago, label):
                if len(valid_data) <= days_ago:
                    return f" {label} 無資料"
                    
                old_price = valid_data[-(days_ago + 1)][1]
                change = price - old_price
                change_pct = (change / old_price) * 100
                
                sign = "+" if change > 0 else ""
                direction_emoji = "🔴" if change > 0 else "🟢"
                if change == 0: direction_emoji = "⚪"
                
                return f"{label} {direction_emoji}{sign}{change_pct:.2f}%"

            price_str = f"{price:.{precision}f}"
            lines = [
                f"{emoji} {title}",
                f"──────────",
                f"🕒 最新: {price_str} {currency}",
                f"",
                f"📊 近期漲跌幅:",
                get_change_str(1, "1日:"),
                get_change_str(5, "1週:"),
                get_change_str(21, "1月:"),
                get_change_str(len(valid_data)-1, "6月:")
            ]
            
            return "\n".join(lines)
        else:
            return f"{emoji} {title}\n──────────\n報價暫時無法使用。"
    except Exception as e:
        return f"{emoji} {title}\n──────────\n無法取得目前報價資訊，請稍後再試。"

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
    parts.append(get_yahoo_data_text('CL=F', 'WTI 輕原油', '🛢️', precision=2))
    parts.append(get_yahoo_data_text('BZ=F', '布蘭特原油', '🛢️', precision=2))
    return "\n\n".join(parts)

def get_10yf_price():
    return get_yahoo_data_text('^TNX', '10年期公債殖利率', '📈', precision=3)

def get_exchange_rates():
    parts = []
    parts.append(get_yahoo_data_text('TWD=X', '美元兌台幣', '💵', precision=3))
    parts.append(get_yahoo_data_text('CHF=X', '美元兌瑞朗', '💷', precision=4))
    parts.append(get_yahoo_data_text('JPY=X', '美元兌日幣', '💴', precision=2))
    return "\n\n".join(parts)

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
    etf_quote_ticker = None if is_operation_report or is_master_holding else parse_etf_quote_command(user_msg)
    print(f"LINE text={user_msg!r} parsed_etf={etf_quote_ticker}", flush=True)
    
    if is_master_holding:
        try:
            from scripts.generate_master_holding_card import generate_master_holding_card, load_cached_master_holding

            try:
                text, output_paths, cache = load_cached_master_holding()
            except Exception:
                text, output_paths = generate_master_holding_card(limit=50)
            messages = [TextSendMessage(text=text)]
            for output_path in output_paths:
                img_url = f"https://linechatbot.duckdns.org/api/webhook/images/{os.path.basename(output_path)}?t={int(time.time())}"
                messages.append(ImageSendMessage(original_content_url=img_url, preview_image_url=img_url))
            line_bot_api.reply_message(event.reply_token, messages)
        except Exception as e:
            print("Master holding card generation failed:", e)
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="吳大師持股暫時無法產生，請稍後再試。")
            )

    elif etf_quote_ticker:
        try:
            from scripts.generate_etf_quote_card import generate_quote_card

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
            status_text = "已重新渲染、推送 GitHub 並廣播。" if pushed else "圖片無變更，已用 GitHub 最新版本廣播。"
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
    elif user_msg.lower() == "id":
        reply_parts = [f"User ID: {event.source.user_id}"]
        if event.source.type == "group":
            reply_parts.append(f"Group ID: {event.source.group_id}")
        elif event.source.type == "room":
            reply_parts.append(f"Room ID: {event.source.room_id}")
            
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
            TextSendMessage(text="抱歉，我目前只聽得懂「油價」、「匯率」、「債券」與「id」！請輸入這些關鍵字來進行查詢。")
        )

@line_handler.add(FollowEvent)
def handle_follow(event):
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text="歡迎加入！🤖\n請在對話框輸入「油價」、「匯率」或「債券」來隨時查詢最新報價，或輸入「id」來取得您的 LINE User ID 與群組 ID。")
    )

@line_handler.add(JoinEvent)
def handle_join(event):
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text="大家好！🤖\n我已經加入這個群組了。輸入「油價」、「匯率」或「債券」來隨時查詢最新報價，輸入「id」來取得目前的使用者與群組 ID。")
    )

# Local deployment entrypoint
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
