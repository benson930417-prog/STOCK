from flask import Flask, request
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import os
import requests
from bs4 import BeautifulSoup

app = Flask(__name__)

# Initialize LineBot APIs using environment variables
line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN', ''))
handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET', ''))

def get_gas_price():
    try:
        url = "https://gas.goodlife.tw/"
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(url, headers=headers, timeout=5)
        soup = BeautifulSoup(res.text, 'html.parser')
        
        main_info = soup.select_one('#main h2')
        main_text = main_info.text.strip() if main_info else ""
        
        cpc = soup.select('#gas-price li')
        prices = []
        for p in cpc:
            text = p.text.strip().replace('\n', ' ').replace('\t', '')
            if text:
                prices.append(text)
                
        reply_text = f"⛽ {main_text}\n" + "\n".join(prices)
        return reply_text
    except Exception as e:
        return "無法取得目前油價資訊，請稍後再試。"

@app.route('/api/webhook', methods=['POST'])
def webhook():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        return "Invalid signature", 400
    return "OK", 200

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_msg = event.message.text.strip()
    
    if user_msg == "油價":
        oil_price_msg = get_gas_price()
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=oil_price_msg)
        )

# Vercel entrypoint for python uses the `app` variable directly.
if __name__ == "__main__":
    app.run(port=8080)
