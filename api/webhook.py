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
                f"📊 歷史漲跌幅:",
                get_change_str(1, "1天:"),
                get_change_str(3, "3天:"),
                get_change_str(5, "5天:"),
                get_change_str(21, "1月:"),
                get_change_str(len(valid_data)-1, "6月:")
            ]
            
            return "\n".join(lines)
        else:
            return f"{emoji} {title}\n──────────\n報價暫時無法使用。"
    except Exception as e:
        return f"{emoji} {title}\n──────────\n無法取得目前報價資訊，請稍後再試。"

def get_oil_price():
    return get_yahoo_data_text('CL=F', 'WTI 輕原油', '🛢️', precision=2)

def get_10yf_price():
    return get_yahoo_data_text('^TNX', '10-Year Yield Futures', '📈', precision=3)

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
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_msg)
        )
    elif user_msg in ["債卷", "債券"]:
        reply_msg = get_10yf_price()
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_msg)
        )
    elif user_msg == "匯率":
        reply_msg = get_exchange_rates()
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_msg)
        )
    elif user_msg == "欸嘿":
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="欸嘿")
        )
    else:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="抱歉，我目前只聽得懂「油價」、「匯率」與「債券」！請輸入這些關鍵字來獲取最新報價。")
        )

@line_handler.add(FollowEvent)
def handle_follow(event):
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text="歡迎加入！🤖\n請在對話框輸入「油價」、「匯率」或「債券」來隨時查詢最新報價。")
    )

# Vercel entrypoint for python uses the `app` variable directly.
if __name__ == "__main__":
    app.run(port=8080)
