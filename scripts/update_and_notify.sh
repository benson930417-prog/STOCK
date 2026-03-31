#!/bin/bash
# This script runs the ETF fetchers, pushes to Github, and sends LINE notifications.

# 1. Enter the project directory and activate Python environment
cd /home/ubuntu/STOCK || exit
source venv/bin/activate

# 2. Add your LINE credentials here (Replace the empty strings with your actual tokens)
LINE_TOKEN="YOUR_LINE_CHANNEL_ACCESS_TOKEN_HERE"
LINE_UID="YOUR_LINE_USER_ID_HERE"
GITHUB_REPO="your-github-username/your-repo-name" # e.g. "benson930417-prog/STOCK"

echo "Running daily ETF fetch..."

# 3. Run the python fetchers
python scripts/fetch_etf_00981A.py
python scripts/fetch_etf_00991A.py

# 4. Check if any new Data was found
if grep -q "NEW DATA FOUND" data/etf_00981A_log.json 2>/dev/null || grep -q "NEW DATA FOUND" data/etf_00991A_log.json 2>/dev/null; then
    echo "New data detected. Generating images..."
    
    # Generate screenshots
    python scripts/generate_etf_summary.py
    
    # Commit and push to GitHub (Required for LINE to read the image URLs)
    echo "Committing to GitHub..."
    git config --global user.name "OCI Server Bot"
    git config --global user.email "oci-bot@localhost"
    git add data/*.json data/*.jpg
    git commit -m "Auto-update ETF Tracker log and image from OCI"
    git push origin main
    
    echo "Sending LINE notification..."
    IMG_981_FILE=$(ls -1r data/etf_00981A_summary_*.jpg | head -1)
    IMG_991_FILE=$(ls -1r data/etf_00991A_summary_*.jpg | head -1)
    
    IMG_981_BASE=$(basename "$IMG_981_FILE")
    IMG_991_BASE=$(basename "$IMG_991_FILE")
    
    IMG_981="https://raw.githubusercontent.com/${GITHUB_REPO}/main/data/$IMG_981_BASE?t=$(date +%s)"
    IMG_991="https://raw.githubusercontent.com/${GITHUB_REPO}/main/data/$IMG_991_BASE?t=$(date +%s)"
    
    DATE_STR_981=$(echo "$IMG_981_BASE" | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}')
    DATE_STR_991=$(echo "$IMG_991_BASE" | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}')
    
    curl -v -X POST https://api.line.me/v2/bot/message/broadcast \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $LINE_TOKEN" \
    -d '{
      "messages": [
        {
          "type": "text",
          "text": "📅 '"$DATE_STR_981"' 主動統一台股增長 (00981A) 操作日報"
        },
        {
          "type": "image",
          "originalContentUrl": "'"$IMG_981"'",
          "previewImageUrl": "'"$IMG_981"'"
        },
        {
          "type": "text",
          "text": "📅 '"$DATE_STR_991"' 主動復華台灣科技優息 (00991A) 操作日報"
        },
        {
          "type": "image",
          "originalContentUrl": "'"$IMG_991"'",
          "previewImageUrl": "'"$IMG_991"'"
        }
      ]
    }'
    
    echo "Done!"
else
    echo "No new ETF data found today. Skipping images and LINE."
    
    # Still push the log files to keep last checked time updated
    git add data/*.json
    git commit -m "Auto-update ETF log timestamps from OCI"
    git push origin main
fi
