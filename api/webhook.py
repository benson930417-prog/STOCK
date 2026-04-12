from flask import Flask, request, send_from_directory
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FollowEvent, JoinEvent, ImageSendMessage
import time
import os
import sys
import requests

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
    
    if user_msg == "油價":
        reply_msg = get_oil_price()
        try:
            snapshot_url = "http://127.0.0.1:5005/snapshot"
            messages = [TextSendMessage(text=reply_msg)]
            
            # WTI and Brent
            pairs = [
                ("oil", "WTI Crude Oil (WTI 輕原油)", "CL=F"),
                ("brent", "Brent Crude Oil (布蘭特原油)", "BZ=F")
            ]
            
            for key, title, sym in pairs:
                d = get_yahoo_data_dict(sym, precision=2)
                color = "#EF4444" if d['raw_change'] >= 0 else "#10B981"
                
                payload = {"key": key, "title": title, "price": f"${d['price']}", "change": d['change'], "color": color}
                res = requests.post(snapshot_url, json=payload, timeout=5).json()
                img_url = f"https://linechatbot.duckdns.org/api/webhook/images/{res['url']}?t={int(time.time())}"
                messages.append(ImageSendMessage(original_content_url=img_url, preview_image_url=img_url))
            
            line_bot_api.reply_message(event.reply_token, messages)
        except Exception as e:
            print("Oil Chart generation failed:", e)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_msg))

    elif user_msg in ["債卷", "債券"]:
        reply_msg = get_10yf_price()
        try:
            d = get_yahoo_data_dict('^TNX', precision=3)
            color = "#EF4444" if d['raw_change'] >= 0 else "#10B981"
            
            snapshot_url = "http://127.0.0.1:5005/snapshot"
            payload = {
                "key": "bond",
                "title": "US 10Y Yield (10年期公債殖利率)",
                "price": f"{d['price']}%",
                "change": d['change'],
                "color": color
            }
            res = requests.post(snapshot_url, json=payload, timeout=5).json()
            
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
            
            # 3 Quick snapshots for the 3 main pairs
            pairs = [
                ("usdtwd", "USD / TWD (美元兌台幣)", "TWD=X", 3),
                ("usdjpy", "USD / JPY (美元兌日幣)", "JPY=X", 2),
                ("usdchf", "USD / CHF (美元兌瑞郎)", "CHF=X", 4)
            ]
            
            for key, title, sym, prec in pairs:
                d = get_yahoo_data_dict(sym, precision=prec)
                color = "#EF4444" if d['raw_change'] >= 0 else "#10B981"
                
                payload = {"key": key, "title": title, "price": f"${d['price']}", "change": d['change'], "color": color}
                res = requests.post(snapshot_url, json=payload, timeout=5).json()
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
