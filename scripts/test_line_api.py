import os
import requests
import json

def test_line_push(channel_access_token, user_id, image_url):
    url = 'https://api.line.me/v2/bot/message/push'
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {channel_access_token}'
    }
    
    # We send a text message and an image message
    data = {
        "to": user_id,
        "messages": [
            {
                "type": "text",
                "text": "Hello! This is a test message from the new LINE Messaging API Bot."
            },
            {
                "type": "image",
                "originalContentUrl": image_url,
                "previewImageUrl": image_url
            }
        ]
    }
    
    print(f"Sending push message to {user_id}...")
    response = requests.post(url, headers=headers, data=json.dumps(data))
    
    if response.status_code == 200:
        print("Success! Message sent.")
    else:
        print(f"Failed to send message. HTTP Code: {response.status_code}")
        print(f"Response: {response.text}")

if __name__ == "__main__":
    print("=== LINE Messaging API Test ===")
    print("To get your tokens:")
    print("1. Go to https://developers.line.biz/console/")
    print("2. Create a provider and a Messaging API channel.")
    print("3. Issue a 'Channel access token (long-lived)' from the Messaging API tab.")
    print("4. Find your 'Your user ID' in the Basic Settings tab.\n")
    
    token = input("Enter your Channel Access Token: ").strip()
    uid = input("Enter your User ID: ").strip()
    
    # A random public image to test
    test_image_url = "https://images.unsplash.com/photo-1526304640581-d334cdbbf45e?ixlib=rb-1.2.1&auto=format&fit=crop&w=800&q=80"
    
    print("\nAttempting to send a test image...")
    test_line_push(token, uid, test_image_url)
