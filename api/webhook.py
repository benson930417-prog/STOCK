from flask import Flask, request
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FollowEvent
import os
import requests

app = Flask(__name__)

# Initialize LineBot APIs using environment variables
line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN', ''))
line_handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET', ''))

def get_oil_price():
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get('https://query1.finance.yahoo.com/v8/finance/chart/CL=F?range=6mo&interval=1d', headers=headers, timeout=5)
        if r.status_code == 200:
            data = r.json()
            result = data['chart']['result'][0]
            meta = result['meta']
            
            price = meta['regularMarketPrice']
            currency = meta['currency']
            
            timestamps = result['timestamp']
            closes = result['indicators']['quote'][0]['close']
            
            # Filter valid data
            valid_data = [(ts, c) for ts, c in zip(timestamps, closes) if c is not None]
            
            if not valid_data:
                return "無有效報價資料。"
                
            def get_change_str(days_ago, label):
                if len(valid_data) <= days_ago:
                    return f" {label}: 無資料"
                    
                old_price = valid_data[-(days_ago + 1)][1]
                change = price - old_price
                change_pct = (change / old_price) * 100
                
                sign = "+" if change > 0 else ""
                emoji = "🔴" if change > 0 else "🟢"
                if change == 0: emoji = "⚪"
                
                return f" {label}: {emoji} {sign}{change:.2f} ({sign}{change_pct:.2f}%)"

            lines = [
                f"🛢️ WTI 輕原油 (CL=F)",
                f"──────────────",
                f"🕒 最新報價: {price:.2f} {currency}",
                f"",
                f"📊 歷史漲跌幅:",
                get_change_str(1, "1天前 (1D)"),
                get_change_str(3, "3天前 (3D)"),
                get_change_str(5, "5天前 (5D)"),
                get_change_str(21, "1個月 (1M)"),
                get_change_str(len(valid_data)-1, "6個月 (6M)")
            ]
            
            return "\n".join(lines)
        else:
            return "Yahoo Finance 報價暫時無法使用。"
    except Exception as e:
        return "無法取得目前油價資訊，請稍後再試。"

@app.route('/', methods=['GET'])
@app.route('/api/webhook', methods=['GET'])
def home():
    return "LINE Bot is running securely!", 200

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
    
    if user_msg in ["油價", "oil", "CL=F"]:
        oil_price_msg = get_oil_price()
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=oil_price_msg)
        )
    else:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="抱歉，我目前只聽得懂「油價」！請輸入油價來獲取最新報價。")
        )

@line_handler.add(FollowEvent)
def handle_follow(event):
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text="歡迎加入！🤖\n請在對話框輸入「油價」來隨時查詢最新的 WTI 原油即時報價。")
    )

# Vercel entrypoint for python uses the `app` variable directly.
if __name__ == "__main__":
    app.run(port=8080)
