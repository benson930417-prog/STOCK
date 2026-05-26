#!/bin/bash
# Daily orchestrator: ETF fetchers + etf_benchmark refresh + git push +
# LINE notify + admin email summary.
#
# Triggered by stock-fetch-1730-tw.timer at 17:30 TPE (09:30 UTC).
# Failure of any single step does NOT abort the rest — every step's status is
# captured into a summary and emailed to ADMIN_EMAIL at the end.

set -u

cd /home/ubuntu/STOCK || exit 1
source venv/bin/activate

# Keep the OCI server in sync with UI updates and code changes from GitHub.
# step1_universe rewrites this generated snapshot; do not let it block deploy pulls.
if ! git diff --quiet -- data/etf_bench/universe.csv || ! git diff --cached --quiet -- data/etf_bench/universe.csv; then
    echo "Restoring generated data/etf_bench/universe.csv before pull."
    git restore --staged --worktree data/etf_bench/universe.csv 2>/dev/null \
        || git checkout -- data/etf_bench/universe.csv
fi
git pull origin main --rebase --autostash
pip install -r requirements.txt -q

SECRETS_FILE="/home/ubuntu/.stock_secrets"
if [ -f "$SECRETS_FILE" ]; then
    # shellcheck disable=SC1090
    source "$SECRETS_FILE"
else
    echo "Warning: Secrets file $SECRETS_FILE not found. LINE/email will fail."
fi

# (GITHUB_REPO no longer needed — daily LINE broadcast serves images via
#  the webhook's duckdns.org URL directly, not GitHub raw.)

# ──────────────────────────────────────────────────────────────────────────
# Logging scaffolding — every step writes to $LOG_DIR/<name>.log and appends
# a one-line status to $SUMMARY_FILE. At end we email both.
# ──────────────────────────────────────────────────────────────────────────
LOG_DIR=$(mktemp -d -t stock_run.XXXX)
SUMMARY_FILE="$LOG_DIR/_summary.txt"
ERRORS_FILE="$LOG_DIR/_errors.txt"
trap 'rm -rf "$LOG_DIR"' EXIT

run_start_utc=$(date -u +"%Y-%m-%d %H:%M:%S UTC")
run_start_epoch=$(date +%s)
overall_status="SUCCESS"
fail_count=0

{
    echo "STOCK daily run summary"
    echo "Run started: $run_start_utc"
    echo "Hostname   : $(hostname)"
    echo
} > "$SUMMARY_FILE"

# Run a labeled command; append OK/FAIL to summary, capture full stderr+stdout
# to a per-step log file, and on failure copy the tail into errors file.
run_step() {
    local label="$1"; shift
    local logfile="$LOG_DIR/${label// /_}.log"
    echo "=== $label ==="
    if "$@" > "$logfile" 2>&1; then
        # Pull useful metrics out of step3/step4/step5 output
        local metrics=""
        case "$label" in
            step3*)
                metrics=$(grep -E "^\s*(OK|EMPTY|FAIL|rows:)" "$logfile" | tr '\n' ' ' | sed 's/  */ /g')
                ;;
            step4*)
                metrics=$(grep -E "^\s*(pass|warn|fail|skip)" "$logfile" | tr '\n' ' ' | sed 's/  */ /g')
                ;;
            step5*)
                metrics=$(grep -E "^\s*[0-9A-Z]+\s+\[(PASS|INFO|WARN|FAIL)\]" "$logfile" | tr '\n' '|' | sed 's/|$//')
                ;;
        esac
        if [ -n "$metrics" ]; then
            printf "  [OK]   %-40s  %s\n" "$label" "$metrics" >> "$SUMMARY_FILE"
        else
            printf "  [OK]   %s\n" "$label" >> "$SUMMARY_FILE"
        fi
        # Echo tail for journal log
        tail -n 3 "$logfile"
        return 0
    else
        local rc=$?
        printf "  [FAIL] %s  (exit=%d)\n" "$label" "$rc" >> "$SUMMARY_FILE"
        {
            echo
            echo "═══════════════════════════════════════════════════"
            echo "FAILURE: $label  (exit=$rc)"
            echo "Last 30 lines of $logfile:"
            echo "───────────────────────────────────────────────────"
            tail -n 30 "$logfile"
            echo
        } >> "$ERRORS_FILE"
        overall_status="PARTIAL_FAIL"
        fail_count=$((fail_count + 1))
        echo "  [FAIL] $label (exit=$rc) — continuing"
        return $rc
    fi
}

# ──────────────────────────────────────────────────────────────────────────
# 1. ETF fetchers (your existing per-ETF scripts)
# ──────────────────────────────────────────────────────────────────────────
if [ "$#" -gt 0 ]; then
    ETFS=("$@")
else
    ETFS=("00981A" "00988A" "00997A" "0050" "00830" "00878" "009805" "009820")
fi

echo "ETF list: ${ETFS[*]}"
{ echo "ETF Fetchers"; echo "──────────"; } >> "$SUMMARY_FILE"

RUN_STARTED_UTC="$(python -c 'from datetime import datetime, timezone; print(datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))')"
FAILED_ETFS=()

for ETF in "${ETFS[@]}"; do
    case "$ETF" in
        00981A|00988A|00997A)
            run_step "fetch $ETF (active)" python "scripts/fetch_etf_${ETF}.py" || FAILED_ETFS+=("$ETF")
            ;;
        0050|00830|00878|009805|009820)
            run_step "fetch $ETF (passive)" python "scripts/fetch_passive_${ETF}.py" || FAILED_ETFS+=("$ETF")
            ;;
        *)
            echo "Skipping unknown ETF: $ETF"
            printf "  [SKIP] unknown ETF: %s\n" "$ETF" >> "$SUMMARY_FILE"
            ;;
    esac
done

# ──────────────────────────────────────────────────────────────────────────
# 2. etf_benchmark refresh (NEW)
# ──────────────────────────────────────────────────────────────────────────
{ echo; echo "etf_benchmark"; echo "──────────"; } >> "$SUMMARY_FILE"
run_step "step3 backfill --incremental" python -m scripts.etf_benchmark.step3_backfill --incremental
run_step "step4 verify (total return)"  python -m scripts.etf_benchmark.step4_verify
run_step "step5 verify_nav"              python -m scripts.etf_benchmark.step5_verify_nav
run_step "step6 regime tagger"           python -m scripts.etf_benchmark.step6_regimes
run_step "generate_market_pulse_summary" python scripts/generate_market_pulse_summary.py

# ──────────────────────────────────────────────────────────────────────────
# 3. Detect which ETFs got NEW DATA this run
# ──────────────────────────────────────────────────────────────────────────
CHANGED_ETFS=()
ACTIVE_NEW_ETFS=()
while IFS= read -r ETF; do
    [ -n "$ETF" ] && CHANGED_ETFS+=("$ETF")
done < <(RUN_STARTED_UTC="$RUN_STARTED_UTC" ETFS="${ETFS[*]}" python - <<'PY'
import json
import os
from datetime import datetime, timezone
from pathlib import Path

run_started = datetime.fromisoformat(os.environ["RUN_STARTED_UTC"].replace("Z", "+00:00"))
for etf in os.environ["ETFS"].split():
    prefix = "passive" if etf in {"0050", "00830", "00878", "009805", "009820"} else "etf"
    path = Path(f"data/{prefix}_{etf}_log.json")
    try:
        log = json.loads(path.read_text(encoding="utf-8"))
        checked = datetime.fromisoformat(str(log.get("last_checked_utc", "")).replace("Z", "+00:00"))
    except Exception:
        continue
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=timezone.utc)
    if checked >= run_started and log.get("status") == "NEW DATA FOUND":
        print(etf)
PY
)

for ETF in "${CHANGED_ETFS[@]}"; do
    case "$ETF" in
        00981A|00988A|00997A)
            ACTIVE_NEW_ETFS+=("$ETF")
            ;;
    esac
done

if [ "${#FAILED_ETFS[@]}" -gt 0 ]; then
    echo "Fetch failed for: ${FAILED_ETFS[*]}"
    FAILED_ETFS_STR="${FAILED_ETFS[*]}" RUN_STARTED_UTC="$RUN_STARTED_UTC" python - <<'PY'
import json
import os
from datetime import datetime, timezone
from pathlib import Path

now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
run_started = os.environ.get("RUN_STARTED_UTC") or now
for etf in os.environ.get("FAILED_ETFS_STR", "").split():
    prefix = "passive" if etf in {"0050", "00830", "00878", "009805", "009820"} else "etf"
    path = Path(f"data/{prefix}_{etf}_log.json")
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        previous = {}
    previous.update(
        {
            "last_checked_utc": now,
            "status": "FETCH FAILED",
            "error": f"{etf} fetch failed during daily job started at {run_started}",
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(previous, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
fi

# ──────────────────────────────────────────────────────────────────────────
# 4. Git push + (if active ETF got new data) LINE broadcast
# ──────────────────────────────────────────────────────────────────────────
git config --global user.name "OCI Server Bot"
git config --global user.email "oci-bot@localhost"

{ echo; echo "Git & LINE"; echo "──────────"; } >> "$SUMMARY_FILE"

if [ "${#CHANGED_ETFS[@]}" -gt 0 ]; then
    echo "New data detected for: ${CHANGED_ETFS[*]}"
    printf "  [INFO] NEW DATA for: %s\n" "${CHANGED_ETFS[*]}" >> "$SUMMARY_FILE"
    if [ "${#ACTIVE_NEW_ETFS[@]}" -gt 0 ]; then
        run_step "generate_etf_summary" python scripts/generate_etf_summary.py
    fi

    git add data/*.json data/summaries/*.jpg 2>/dev/null
    if git commit -m "Auto-update ETF data and summary images from OCI" 2>&1; then
        printf "  [OK]   git commit\n" >> "$SUMMARY_FILE"
    else
        printf "  [INFO] git: no changes to commit\n" >> "$SUMMARY_FILE"
    fi
    run_step "git push origin main" git push origin main

    if [ "${#ACTIVE_NEW_ETFS[@]}" -gt 0 ] && [ -n "${LINE_TOKEN:-}" ]; then
        export LINE_ETFS="${ACTIVE_NEW_ETFS[*]}"
        export LINE_TOKEN
        # Daily broadcast — images served directly by the webhook via duckdns.org,
        # NOT GitHub raw URL. This avoids the git commit+push+CDN-wait dance and
        # the gitignore-mismatch class of bug that broke the daily images.
        run_step "LINE broadcast active reports" python - <<'PY'
import json
import os
import time
from urllib import request

token = os.environ["LINE_TOKEN"]
tickers = os.environ["LINE_ETFS"].split()
names = {
    "00981A": "主動統一台股增長",
    "00988A": "主動統一全球創新",
    "00997A": "主動群益美國增長",
}

messages = []
cache_buster = int(time.time())
for ticker in tickers:
    history_path = f"data/etf_{ticker}_history.json"
    with open(history_path, encoding="utf-8") as fh:
        date_str = max(json.load(fh).keys())
    img_url = (
        f"https://linechatbot.duckdns.org/api/webhook/summaries/"
        f"etf_{ticker}_summary_latest.jpg?t={cache_buster}"
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
    elif [ "${#ACTIVE_NEW_ETFS[@]}" -gt 0 ]; then
        printf "  [SKIP] LINE broadcast: LINE_TOKEN not set\n" >> "$SUMMARY_FILE"
        echo "LINE_TOKEN is not set. Skipping LINE notification."
    fi
else
    echo "No new ETF data found. Pushing log timestamps only."
    printf "  [INFO] no new ETF data\n" >> "$SUMMARY_FILE"
    git add data/*.json data/summaries/*.jpg 2>/dev/null
    git commit -m "Auto-update ETF log timestamps and market pulse summary from OCI" 2>/dev/null || true
    run_step "git push origin main (log-only)" git push origin main
fi

# ──────────────────────────────────────────────────────────────────────────
# 5. Send admin email summary (always, even on full success)
# ──────────────────────────────────────────────────────────────────────────
run_end_epoch=$(date +%s)
duration=$((run_end_epoch - run_start_epoch))

{
    echo
    echo "──────────"
    echo "Duration   : ${duration}s"
    echo "Overall    : $overall_status"
    echo "Failed steps: $fail_count"
} >> "$SUMMARY_FILE"

# Compose final body: summary always, errors appended if any
FINAL_BODY="$LOG_DIR/_email_body.txt"
cat "$SUMMARY_FILE" > "$FINAL_BODY"
if [ -s "$ERRORS_FILE" ]; then
    {
        echo
        echo "════════════ FAILURE DETAILS ════════════"
        cat "$ERRORS_FILE"
    } >> "$FINAL_BODY"
fi

SUBJECT="[STOCK] daily run — $overall_status ($(date +"%Y-%m-%d %H:%M") TPE)"
if [ -n "${GMAIL_APP_PASSWORD:-}" ]; then
    GMAIL_APP_PASSWORD="$GMAIL_APP_PASSWORD" \
    ADMIN_EMAIL="${ADMIN_EMAIL:-benson930417@gmail.com}" \
    GMAIL_FROM="${GMAIL_FROM:-${ADMIN_EMAIL:-benson930417@gmail.com}}" \
    python scripts/admin_email.py \
        --subject "$SUBJECT" \
        --body-file "$FINAL_BODY" \
        || echo "WARN: admin email failed (non-fatal)"
else
    echo "GMAIL_APP_PASSWORD not set in $SECRETS_FILE — skipping admin email"
fi

echo "Run complete: $overall_status ($fail_count failed steps, ${duration}s)"
