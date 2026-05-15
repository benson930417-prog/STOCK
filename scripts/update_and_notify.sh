#!/bin/bash
# Run selected ETF fetchers, push new data to GitHub, and send LINE notifications.

set -u

cd /home/ubuntu/STOCK || exit 1
source venv/bin/activate

# Keep the OCI server in sync with UI updates and code changes from GitHub.
git pull origin main --rebase --autostash
pip install -r requirements.txt -q

SECRETS_FILE="/home/ubuntu/.stock_secrets"
if [ -f "$SECRETS_FILE" ]; then
    source "$SECRETS_FILE"
else
    echo "Warning: Secrets file $SECRETS_FILE not found. LINE notification will fail."
fi

GITHUB_REPO="${GITHUB_REPO:-benson930417-prog/STOCK}"

if [ "$#" -gt 0 ]; then
    ETFS=("$@")
else
    ETFS=("00981A" "00997A" "0050" "00830")
fi

echo "Running ETF fetch for: ${ETFS[*]}"

for ETF in "${ETFS[@]}"; do
    case "$ETF" in
        00981A|00997A)
            python "scripts/fetch_etf_${ETF}.py"
            ;;
        0050|00830)
            python "scripts/fetch_passive_${ETF}.py"
            ;;
        *)
            echo "Skipping unknown ETF: $ETF"
            ;;
    esac
done

CHANGED_ETFS=()
ACTIVE_NEW_ETFS=()
for ETF in "${ETFS[@]}"; do
    if [ "$ETF" = "0050" ] || [ "$ETF" = "00830" ]; then
        LOG_FILE="data/passive_${ETF}_log.json"
    else
        LOG_FILE="data/etf_${ETF}_log.json"
    fi

    if grep -q "NEW DATA FOUND" "$LOG_FILE" 2>/dev/null; then
        CHANGED_ETFS+=("$ETF")
        case "$ETF" in
            00981A|00997A)
                ACTIVE_NEW_ETFS+=("$ETF")
                ;;
        esac
    fi
done

git config --global user.name "OCI Server Bot"
git config --global user.email "oci-bot@localhost"

if [ "${#CHANGED_ETFS[@]}" -gt 0 ]; then
    echo "New data detected for: ${CHANGED_ETFS[*]}"
    if [ "${#ACTIVE_NEW_ETFS[@]}" -gt 0 ]; then
        python scripts/generate_etf_summary.py
    fi

    git add data/*.json data/summaries/*.jpg
    git commit -m "Auto-update ETF data and summary images from OCI" || true
    git push origin main

    if [ "${#ACTIVE_NEW_ETFS[@]}" -gt 0 ] && [ -n "${LINE_TOKEN:-}" ]; then
        export GITHUB_REPO
        export LINE_ETFS="${ACTIVE_NEW_ETFS[*]}"
        export LINE_TOKEN
        python - <<'PY'
import json
import os
import time
from urllib import request

repo = os.environ["GITHUB_REPO"]
token = os.environ["LINE_TOKEN"]
tickers = os.environ["LINE_ETFS"].split()
names = {
    "00981A": "主動統一台股增長",
    "00997A": "主動群益美國增長",
}

messages = []
cache_buster = int(time.time())
for ticker in tickers:
    history_path = f"data/etf_{ticker}_history.json"
    with open(history_path, encoding="utf-8") as fh:
        date_str = max(json.load(fh).keys())
    img_url = (
        f"https://raw.githubusercontent.com/{repo}/main/"
        f"data/summaries/etf_{ticker}_summary_latest.jpg?t={cache_buster}"
    )
    messages.append({
        "type": "text",
        "text": f"{date_str} {names.get(ticker, ticker)} ({ticker}) 操作日報",
    })
    messages.append({
        "type": "image",
        "originalContentUrl": img_url,
        "previewImageUrl": img_url,
    })

payload = json.dumps({"messages": messages}, ensure_ascii=False).encode("utf-8")
req = request.Request(
    "https://api.line.me/v2/bot/message/broadcast",
    data=payload,
    headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    },
    method="POST",
)
with request.urlopen(req, timeout=20) as resp:
    print(resp.status, resp.read().decode("utf-8", errors="replace"))
PY
    else
        echo "LINE_TOKEN is not set. Skipping LINE notification."
    fi
else
    echo "No new ETF data found. Pushing log timestamps only."
    git add data/*.json
    git commit -m "Auto-update ETF log timestamps from OCI" || true
    git push origin main
fi
