from flask import Flask, request
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import os
import requests

app = Flask(__name__)

# Initialize LineBot APIs using environment variables
line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN', ''))
line_handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET', ''))

def get_oil_price():
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get('https://query1.finance.yahoo.com/v8/finance/chart/CL=F', headers=headers, timeout=5)
        if r.status_code == 200:
            data = r.json()
            meta = data['chart']['result'][0]['meta']
            price = meta['regularMarketPrice']
            currency = meta['currency']
            
            prev_close = meta['chartPreviousClose']
            change = price - prev_close
            change_pct = (change / prev_close) * 100
            
            sign = "+" if change > 0 else ""
            emoji = "🔴" if change > 0 else "🟢"
            if change == 0: emoji = "⚪"
            
            return f"🛢️ 輕原油 (WTI)\n{price:.2f} {currency}\n{emoji} {sign}{change:.2f} ({sign}{change_pct:.2f}%)"
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

# Vercel entrypoint for python uses the `app` variable directly.
if __name__ == "__main__":
    app.run(port=8080)
